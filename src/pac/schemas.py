"""Data contracts shared by the classifier, eval harness, dataset and API.

These models are the single source of truth: the API exposes them, the dataset is validated
against them, and the LLM's output is parsed into :class:`Decision`.
"""

from __future__ import annotations

import re
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

EventKind = Literal["user_input", "model_output", "tool_call", "tool_response"]
Verdict = Literal["allow", "violation", "policy_silent"]
LabelConfidence = Literal["high", "medium", "contested"]
Split = Literal["dev", "test"]

EVENT_KINDS: tuple[EventKind, ...] = ("user_input", "model_output", "tool_call", "tool_response")
VERDICTS: tuple[Verdict, ...] = ("allow", "violation", "policy_silent")

RULE_ID_PATTERN = re.compile(r"^R\d{1,2}$")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TraceEvent(_Strict):
    """One event from an agent trace.

    ``user_input`` / ``model_output`` carry ``content`` only. ``tool_call`` carries ``tool_name``
    and ``arguments``. ``tool_response`` carries ``tool_name`` and ``content`` (the raw tool
    output, which is often JSON text and may be truncated, as in real traces).
    """

    kind: EventKind
    content: str | None = None
    tool_name: str | None = None
    arguments: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _fields_match_kind(self) -> Self:
        match self.kind:
            case "user_input" | "model_output":
                if self.content is None:
                    raise ValueError(f"{self.kind} requires 'content'")
                if self.tool_name is not None or self.arguments is not None:
                    raise ValueError(f"{self.kind} must not carry tool fields")
            case "tool_call":
                if self.tool_name is None or self.arguments is None:
                    raise ValueError("tool_call requires 'tool_name' and 'arguments'")
                if self.content is not None:
                    raise ValueError("tool_call must not carry 'content'")
            case "tool_response":
                if self.tool_name is None or self.content is None:
                    raise ValueError("tool_response requires 'tool_name' and 'content'")
                if self.arguments is not None:
                    raise ValueError("tool_response must not carry 'arguments'")
        return self


class Session(_Strict):
    """Session envelope attached to every event by the chat platform (trusted)."""

    verified: bool = False
    verified_customer_id: str | None = None
    email_on_file: str | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        identity = (self.verified_customer_id, self.email_on_file)
        if self.verified and None in identity:
            raise ValueError("a verified session needs verified_customer_id and email_on_file")
        if not self.verified and identity != (None, None):
            raise ValueError("an unverified session must not carry a customer identity")
        return self


class ClassificationRequest(_Strict):
    """What the classifier sees: one event, its session envelope, and optional history.

    ``history`` is empty by default; it is only populated for the history ablation.
    """

    event: TraceEvent
    session: Session = Field(default_factory=Session)
    history: tuple[TraceEvent, ...] = ()


class Decision(_Strict):
    """The judgement itself — exactly what the LLM is asked to produce.

    Field order matters: the rationale comes first so the model reasons before committing.
    """

    rationale: str = Field(min_length=1, max_length=2000)
    verdict: Verdict
    rule_ids: tuple[str, ...] = ()
    confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _rules_are_well_formed(self) -> Self:
        bad = [r for r in self.rule_ids if not RULE_ID_PATTERN.match(r)]
        if bad:
            raise ValueError(f"malformed rule ids: {bad}")
        if self.verdict == "violation" and not self.rule_ids:
            raise ValueError("a violation must cite at least one rule")
        return self


class Usage(_Strict):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


class Classification(_Strict):
    """A :class:`Decision` plus the operational metadata the harness records around it."""

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


class LabeledCase(_Strict):
    """One eval case: a request, its gold label, and the reasoning behind that label."""

    id: str = Field(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")
    split: Split
    request: ClassificationRequest
    label: Verdict
    rule_ids: tuple[str, ...] = ()
    label_confidence: LabelConfidence
    rationale: str = Field(min_length=1)
    tags: tuple[str, ...] = ()
    origin: Literal["targeted", "transcript"] = "targeted"

    @model_validator(mode="after")
    def _label_has_rules(self) -> Self:
        if self.label == "violation" and not self.rule_ids:
            raise ValueError(f"{self.id}: violation label must cite a rule")
        return self
