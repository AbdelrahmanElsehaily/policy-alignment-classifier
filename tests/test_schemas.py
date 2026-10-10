from typing import Any

import pytest
from pydantic import ValidationError

from pac.schemas import ClassificationRequest, Decision, LabeledCase, Session, TraceEvent


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


def test_decision_requires_rule_for_violation() -> None:
    with pytest.raises(ValidationError):
        Decision(rationale="x", verdict="violation", confidence=0.9)
    with pytest.raises(ValidationError):
        Decision(rationale="x", verdict="violation", rule_ids=("rule-1",), confidence=0.9)
    with pytest.raises(ValidationError):
        Decision(rationale="x", verdict="allow", confidence=1.5)
    assert Decision(rationale="x", verdict="allow", confidence=0.5).rule_ids == ()


def test_decision_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        Decision.model_validate(
            {"rationale": "x", "verdict": "allow", "confidence": 0.5, "extra": 1}
        )


def test_labeled_case_round_trip() -> None:
    case = LabeledCase(
        id="r4-secret-in-tool-response",
        split="dev",
        request=ClassificationRequest(
            event=TraceEvent(kind="tool_response", tool_name="search_kb", content="sk_live_x")
        ),
        label="violation",
        rule_ids=("R4",),
        label_confidence="high",
        rationale="A secret in a tool result is a leak.",
    )
    assert LabeledCase.model_validate_json(case.model_dump_json()) == case


def test_labeled_case_id_format() -> None:
    with pytest.raises(ValidationError):
        LabeledCase(
            id="Bad ID",
            split="dev",
            request=ClassificationRequest(event=TraceEvent(kind="user_input", content="x")),
            label="allow",
            label_confidence="high",
            rationale="x",
        )
