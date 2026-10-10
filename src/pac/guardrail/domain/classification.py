"""The outcome of classifying an event, and the contract every guardrail implements."""

from __future__ import annotations

from typing import Protocol

from pydantic import Field

from pac.guardrail.domain.decision import Decision
from pac.guardrail.domain.events import ClassificationRequest
from pac.guardrail.domain.value_object import ValueObject


class Usage(ValueObject):
    """Tokens spent (Anthropic semantics: ``input_tokens`` excludes cached tokens)."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_input_tokens=self.cache_read_input_tokens + other.cache_read_input_tokens,
            cache_creation_input_tokens=self.cache_creation_input_tokens
            + other.cache_creation_input_tokens,
        )


class Classification(ValueObject):
    """A :class:`Decision` plus the operational facts recorded around it."""

    decision: Decision
    model: str
    prompt_version: str
    policy_version: str
    latency_ms: float
    # Time spent waiting on the model across all attempts, as originally measured (also for
    # results replayed from the cache). ``latency_ms`` is this call's own wall time.
    model_latency_ms: float = 0.0
    usage: Usage = Field(default_factory=Usage)
    cached: bool = False
    error: str | None = None


class EventClassifier(Protocol):
    """A guardrail: one event in, one classification out. The LLM classifier and the regex
    baseline both implement it, so evaluation and delivery never depend on which one runs."""

    @property
    def name(self) -> str: ...

    async def classify(self, request: ClassificationRequest) -> Classification: ...
