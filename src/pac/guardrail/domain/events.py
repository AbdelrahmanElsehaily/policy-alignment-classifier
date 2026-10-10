"""What the guardrail judges: one agent trace event, in its session."""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import Field, model_validator

from pac.guardrail.domain.value_object import ValueObject

EventKind = Literal["user_input", "model_output", "tool_call", "tool_response"]
EVENT_KINDS: tuple[EventKind, ...] = ("user_input", "model_output", "tool_call", "tool_response")


class TraceEvent(ValueObject):
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


class Session(ValueObject):
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


class ClassificationRequest(ValueObject):
    """What the classifier sees: one event, its session envelope, and optional history.

    ``history`` is empty by default; it is only populated for the history ablation.
    """

    event: TraceEvent
    session: Session = Field(default_factory=Session)
    history: tuple[TraceEvent, ...] = ()

    def without_history(self) -> ClassificationRequest:
        return self.model_copy(update={"history": ()})
