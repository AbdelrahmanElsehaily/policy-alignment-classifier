"""The policy alignment classifier: one trace event in, one auditable verdict out."""

from __future__ import annotations

import asyncio
import time
from typing import Protocol

from pydantic import ValidationError

from pac.backends import build_backend
from pac.backends.base import BackendError, Completion, LLMBackend
from pac.backends.cache import CacheMiss
from pac.policy import Policy
from pac.prompting import PromptTemplate, parse_decision
from pac.schemas import Classification, ClassificationRequest, Decision, Usage
from pac.settings import Settings


class EventClassifier(Protocol):
    """Anything the eval harness and API can run: the LLM classifier or a baseline."""

    @property
    def name(self) -> str: ...

    async def classify(self, request: ClassificationRequest) -> Classification: ...


def fail_closed(reason: str) -> Decision:
    """The decision returned when the classifier itself fails.

    A guardrail that fails open is not a guardrail, but failing to ``violation`` would block
    legitimate traffic on every outage. ``policy_silent`` routes the event to a human.
    """
    return Decision(
        rationale=f"Classifier error, routed to human review: {reason}",
        verdict="policy_silent",
        rule_ids=(),
        confidence=0.0,
    )


def _add(a: Usage, b: Usage) -> Usage:
    return Usage(
        input_tokens=a.input_tokens + b.input_tokens,
        output_tokens=a.output_tokens + b.output_tokens,
        cache_read_input_tokens=a.cache_read_input_tokens + b.cache_read_input_tokens,
        cache_creation_input_tokens=a.cache_creation_input_tokens + b.cache_creation_input_tokens,
    )


class LLMClassifier:
    """Prompted-LLM classifier with bounded retries and fail-closed behaviour.

    Attempt policy:
    - provider error or timeout: retry the identical request (failures are never cached);
    - unparseable output: retry with a repair instruction appended — a *different* request, so
      a deterministic model or a cached bad answer does not just repeat itself;
    - replay-cache miss: no retry, there is nothing to retry against.
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
        cached = True
        repair = False
        error = "no attempts made"

        for _ in range(self._max_attempts):
            llm_request = self._prompt.render(request, repair=repair)
            try:
                completion: Completion = await asyncio.wait_for(
                    self._backend.complete(llm_request), timeout=self._timeout_s
                )
            except CacheMiss as exc:
                error = str(exc)
                break
            except TimeoutError:
                error = f"timed out after {self._timeout_s:.0f}s"
                continue
            except BackendError as exc:
                error = str(exc)
                continue

            usage = _add(usage, completion.usage)
            cached = cached and completion.cached
            try:
                decision = parse_decision(completion.text, self._policy.rule_ids)
            except (ValueError, ValidationError) as exc:
                error = f"unparseable output: {_first_line(exc)}"
                repair = True
                continue
            return self._result(decision, started, usage, cached=cached)

        return self._result(fail_closed(error), started, usage, cached=False, error=error)

    def _result(
        self,
        decision: Decision,
        started: float,
        usage: Usage,
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
            usage=usage,
            cached=cached,
            error=error,
        )


def _first_line(exc: Exception) -> str:
    return str(exc).strip().splitlines()[0][:200]


def build_classifier(settings: Settings, backend: LLMBackend | None = None) -> LLMClassifier:
    """Wire the configured backend, policy and prompt into a classifier."""
    policy = Policy.load(settings.policy_path)
    prompt = PromptTemplate.load(
        settings.prompt_path, policy, max_tokens=settings.max_output_tokens
    )
    return LLMClassifier(
        backend if backend is not None else build_backend(settings),
        policy,
        prompt,
        timeout_s=settings.timeout_s,
    )
