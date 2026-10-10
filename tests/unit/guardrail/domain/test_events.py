from typing import Any

import pytest
from pydantic import ValidationError

from pac.guardrail.domain.events import ClassificationRequest, Session, TraceEvent


@pytest.mark.parametrize(
    "fields",
    [
        {"kind": "user_input", "content": "hi"},
        {"kind": "model_output", "content": "hello"},
        {"kind": "tool_call", "tool_name": "lookup_order", "arguments": {"order_id": "A1"}},
        {"kind": "tool_response", "tool_name": "lookup_order", "content": "{}"},
    ],
)
def test_valid_events(fields: dict[str, Any]) -> None:
    assert TraceEvent(**fields).kind == fields["kind"]


@pytest.mark.parametrize(
    "fields",
    [
        {"kind": "user_input"},
        {"kind": "user_input", "content": "hi", "tool_name": "x"},
        {"kind": "tool_call", "tool_name": "x"},
        {"kind": "tool_call", "tool_name": "x", "arguments": {}, "content": "x"},
        {"kind": "tool_response", "content": "x"},
        {"kind": "tool_response", "tool_name": "x", "content": "x", "arguments": {}},
        {"kind": "telepathy", "content": "x"},
    ],
)
def test_invalid_events(fields: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        TraceEvent(**fields)


def test_session_consistency() -> None:
    Session()
    Session(verified=True, verified_customer_id="C1", email_on_file="a@b.example")
    with pytest.raises(ValidationError):
        Session(verified=True)
    with pytest.raises(ValidationError):
        Session(verified=False, verified_customer_id="C1")


def test_request_defaults_to_unverified_without_history() -> None:
    req = ClassificationRequest(event=TraceEvent(kind="user_input", content="hi"))
    assert not req.session.verified
    assert req.history == ()
