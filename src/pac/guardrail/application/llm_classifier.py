"""Use case: classify one trace event with a prompted LLM, failing closed on anything odd."""

from __future__ import annotations

import asyncio
import logging
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
from pac.guardrail.domain.events import ClassificationRequest, TraceEvent
from pac.guardrail.domain.policy import Policy

logger = logging.getLogger(__name__)


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
        max_event_chars: int | None = None,
    ) -> None:
        self._backend = backend
        self._policy = policy
        self._prompt = prompt
        self._timeout_s = timeout_s
        self._max_attempts = max_attempts
        # Above this many characters, an event is judged in overlapping parts (None: never).
        self._max_event_chars = max_event_chars

    @property
    def name(self) -> str:
        return self._backend.model

    def with_policy(self, policy: Policy, prompt: PromptTemplate) -> LLMClassifier:
        """The same classifier (backend, timeout, attempts) judging by another policy."""
        return LLMClassifier(
            self._backend,
            policy,
            prompt,
            timeout_s=self._timeout_s,
            max_attempts=self._max_attempts,
            max_event_chars=self._max_event_chars,
        )

    @property
    def prompt(self) -> PromptTemplate:
        return self._prompt

    async def classify(self, request: ClassificationRequest) -> Classification:
        limit = self._max_event_chars
        if limit is not None and event_chars(request.event) > limit:
            return await self._classify_in_parts(request, limit)
        return await self._classify_one(request)

    async def _classify_in_parts(
        self, request: ClassificationRequest, limit: int
    ) -> Classification:
        """Judge a long event as overlapping parts, all at once, and combine the verdicts.

        A violation anywhere makes the event a violation; otherwise a part the policy can't
        decide holds it; any part that fails fails the whole event closed. Latency is the
        slowest part's, since they run in parallel; tokens are the sum.
        """
        started = time.perf_counter()
        parts = split_event(request.event, limit, overlap=min(1000, limit // 4))
        results = await asyncio.gather(
            *(self._classify_one(request.model_copy(update={"event": part})) for part in parts)
        )
        usage = Usage()
        for r in results:
            usage += r.usage
        latency = max(r.model_latency_ms for r in results)
        cached = all(r.cached for r in results)
        failed = [r for r in results if r.error is not None]
        ranked = [(i, r) for i, r in enumerate(results) if r.error is None]
        for verdict in ("violation", "policy_silent"):
            hits = [(i, r) for i, r in ranked if r.decision.verdict == verdict]
            if hits and (verdict == "violation" or not failed):
                first_i, first = hits[0]
                rules = tuple(dict.fromkeys(rule for _, r in hits for rule in r.decision.rule_ids))
                where = f"Part {first_i + 1} of {len(parts)}"
                decision = first.decision.model_copy(
                    update={
                        "rule_ids": rules,
                        "rationale": f"{where}: {first.decision.rationale}",
                    }
                )
                return self._result(decision, started, usage, latency, cached=cached)
        if failed:
            error = f"{len(failed)} of {len(parts)} parts failed: {failed[0].error}"
            return self._result(
                Decision.fail_closed(error), started, usage, latency, cached=cached, error=error
            )
        decision = results[0].decision.model_copy(
            update={
                "rationale": f"All {len(parts)} parts allowed. {results[0].decision.rationale}",
                "confidence": min(r.decision.confidence for r in results),
            }
        )
        return self._result(decision, started, usage, latency, cached=cached)

    async def _classify_one(self, request: ClassificationRequest) -> Classification:
        started = time.perf_counter()
        usage = Usage()
        model_latency_ms = 0.0
        cached = True
        completions = 0
        repair = False
        error = "no attempts made"

        kind = request.event.kind
        for attempt in range(1, self._max_attempts + 1):
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
                self._log_retry(attempt, error)
                continue
            except BackendError as exc:
                error = str(exc)
                self._log_retry(attempt, error)
                continue
            except Exception as exc:  # a guardrail must fail closed on anything
                error = f"unexpected backend failure: {type(exc).__name__}: {exc}"[:300]
                logger.exception("unexpected backend failure", extra={"attempt": attempt})
                continue

            completions += 1
            usage += completion.usage
            model_latency_ms += completion.latency_ms
            cached = cached and completion.cached
            try:
                decision = parse_decision(completion.text, self._policy)
            except (ValueError, ValidationError) as exc:
                error = f"unparseable output: {_first_line(exc)}"
                repair = True
                self._log_retry(attempt, error)
                continue
            result = self._result(decision, started, usage, model_latency_ms, cached=cached)
            logger.debug(
                "classified",
                extra={
                    "kind": kind,
                    "verdict": decision.verdict,
                    "rules": ",".join(decision.rule_ids) or "-",
                    "attempts": attempt,
                    "model_ms": round(model_latency_ms),
                    "cached": cached,
                },
            )
            return result

        logger.error(
            "failed closed: held for a human",
            extra={"kind": kind, "model": self._backend.model, "reason": error},
        )
        return self._result(
            Decision.fail_closed(error),
            started,
            usage,
            model_latency_ms,
            # Nothing came back at all (provider errors, timeouts): a live call, not a replay.
            cached=cached and completions > 0,
            error=error,
        )

    def _log_retry(self, attempt: int, reason: str) -> None:
        last = attempt >= self._max_attempts
        logger.warning(
            "attempt failed" + ("" if last else "; retrying"),
            extra={"attempt": f"{attempt}/{self._max_attempts}", "reason": reason[:200]},
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


def event_chars(event: TraceEvent) -> int:
    """How long an event is: its text, or for a tool call its longest argument."""
    if event.content is not None:
        return len(event.content)
    values = [v for v in (event.arguments or {}).values() if isinstance(v, str)]
    return max((len(v) for v in values), default=0)


def split_event(event: TraceEvent, limit: int, *, overlap: int) -> list[TraceEvent]:
    """The event as overlapping parts of at most ``limit`` characters, each saying which part it is.

    For a tool call, the longest string argument is split and the other arguments repeat in every
    part, so each part is still a complete call to judge.
    """
    if event.content is not None:
        field, text = None, event.content
    else:
        args = event.arguments or {}
        field = max((k for k, v in args.items() if isinstance(v, str)), key=lambda k: len(args[k]))
        text = args[field]
    step = max(1, limit - overlap)
    windows = [text[i : i + limit] for i in range(0, max(1, len(text) - overlap), step)]
    parts = []
    for n, window in enumerate(windows, start=1):
        marked = f"[part {n} of {len(windows)} of a longer {event.kind}]\n{window}"
        if field is None:
            parts.append(event.model_copy(update={"content": marked}))
        else:
            args = dict(event.arguments or {})
            args[field] = marked
            parts.append(event.model_copy(update={"arguments": args}))
    return parts


def _first_line(exc: Exception) -> str:
    """A one-line, specific reason (pydantic's first line alone just says '1 validation error')."""
    if isinstance(exc, ValidationError) and exc.errors():
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"]) or "output"
        return f"{where}: {first['msg']}"[:200]
    return str(exc).strip().splitlines()[0][:200]
