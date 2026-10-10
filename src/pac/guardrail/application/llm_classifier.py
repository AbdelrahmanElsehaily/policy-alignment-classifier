"""Use case: classify one trace event with a prompted LLM, failing closed on anything odd."""

from __future__ import annotations

import asyncio
import time

from pydantic import ValidationError

from pac.guardrail.application.ports import (
    BackendError,
    Completion,
    LLMBackend,
    NonRetryableBackendError,
)
from pac.guardrail.application.prompting import PromptTemplate, parse_decision
from pac.guardrail.domain.classification import Classification, Usage
from pac.guardrail.domain.decision import Decision
from pac.guardrail.domain.events import ClassificationRequest
from pac.guardrail.domain.policy import Policy


class LLMClassifier:
    """Prompted-LLM implementation of :class:`~pac.guardrail.domain.classification.EventClassifier`.

    Attempt policy:
    - provider error or timeout: retry the identical request (failures are never cached);
    - unparseable output: retry with a repair instruction appended — a *different* request, so
      a deterministic model or a cached bad answer does not just repeat itself;
    - non-retryable failure (e.g. a replay-cache miss): stop, there is nothing to retry against.
    Whatever happens, the result is a valid :class:`Classification`; failures fail closed.
    """

    def __init__(
        self,
        backend: LLMBackend,
        policy: Policy,
        prompt: PromptTemplate,
        *,
        timeout_s: float = 60.0,
        max_attempts: int = 2,
    ) -> None:
        self._backend = backend
        self._policy = policy
        self._prompt = prompt
        self._timeout_s = timeout_s
        self._max_attempts = max_attempts

    @property
    def name(self) -> str:
        return self._backend.model

    @property
    def prompt(self) -> PromptTemplate:
        return self._prompt

    async def classify(self, request: ClassificationRequest) -> Classification:
        started = time.perf_counter()
        usage = Usage()
        model_latency_ms = 0.0
        cached = True
        repair = False
        error = "no attempts made"

        for _ in range(self._max_attempts):
            llm_request = self._prompt.render(request, repair=repair)
            try:
                completion: Completion = await asyncio.wait_for(
                    self._backend.complete(llm_request), timeout=self._timeout_s
                )
            except NonRetryableBackendError as exc:
                error = str(exc)
                break
            except TimeoutError:
                error = f"timed out after {self._timeout_s:.0f}s"
                continue
            except BackendError as exc:
                error = str(exc)
                continue
            except Exception as exc:  # a guardrail must fail closed on anything
                error = f"unexpected backend failure: {type(exc).__name__}: {exc}"[:300]
                continue

            usage += completion.usage
            model_latency_ms += completion.latency_ms
            cached = cached and completion.cached
            try:
                decision = parse_decision(completion.text, self._policy)
            except (ValueError, ValidationError) as exc:
                error = f"unparseable output: {_first_line(exc)}"
                repair = True
                continue
            return self._result(decision, started, usage, model_latency_ms, cached=cached)

        return self._result(
            Decision.fail_closed(error),
            started,
            usage,
            model_latency_ms,
            cached=cached,
            error=error,
        )

    def _result(
        self,
        decision: Decision,
        started: float,
        usage: Usage,
        model_latency_ms: float,
        *,
        cached: bool,
        error: str | None = None,
    ) -> Classification:
        return Classification(
            decision=decision,
            model=self._backend.model,
            prompt_version=self._prompt.version,
            policy_version=self._policy.version,
            latency_ms=(time.perf_counter() - started) * 1000,
            model_latency_ms=model_latency_ms,
            usage=usage,
            cached=cached,
            error=error,
        )


def _first_line(exc: Exception) -> str:
    """A one-line, specific reason (pydantic's first line alone just says '1 validation error')."""
    if isinstance(exc, ValidationError) and exc.errors():
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"]) or "output"
        return f"{where}: {first['msg']}"[:200]
    return str(exc).strip().splitlines()[0][:200]
