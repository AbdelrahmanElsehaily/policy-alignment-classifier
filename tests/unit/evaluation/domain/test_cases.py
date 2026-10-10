import pytest
from pydantic import ValidationError

from pac.evaluation.domain.cases import (
    DatasetError,
    LabeledCase,
    check_against_policy,
    ensure_unique_ids,
)
from pac.guardrail.domain.events import ClassificationRequest, TraceEvent
from pac.guardrail.domain.policy import Policy


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


def _case(case_id: str, rules: tuple[str, ...] = ()) -> LabeledCase:
    return LabeledCase(
        id=case_id,
        split="dev",
        request=ClassificationRequest(event=TraceEvent(kind="user_input", content="x")),
        label="violation" if rules else "allow",
        rule_ids=rules,
        label_confidence="high",
        rationale="x",
    )


def test_dataset_invariants(policy: Policy) -> None:
    ensure_unique_ids([_case("a"), _case("b")])
    with pytest.raises(DatasetError, match="duplicate"):
        ensure_unique_ids([_case("a"), _case("a")])
    check_against_policy([_case("a", ("R5",))], policy)
    with pytest.raises(DatasetError, match="R99"):
        check_against_policy([_case("a", ("R99",))], policy)


def test_request_for_strips_history_unless_ablating() -> None:
    history = (TraceEvent(kind="user_input", content="earlier"),)
    case = _case("a").model_copy(
        update={
            "request": ClassificationRequest(
                event=TraceEvent(kind="user_input", content="x"), history=history
            )
        }
    )
    assert case.request_for(with_history=False).history == ()
    assert case.request_for(with_history=True).history == history
