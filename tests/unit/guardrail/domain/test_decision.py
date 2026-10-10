import pytest
from pydantic import ValidationError

from pac.guardrail.domain.classification import Usage
from pac.guardrail.domain.decision import Decision


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


def test_fail_closed_routes_to_human() -> None:
    decision = Decision.fail_closed("timed out")
    assert decision.verdict == "policy_silent"
    assert decision.confidence == 0.0
    assert "timed out" in decision.rationale
    assert len(Decision.fail_closed("x" * 5000).rationale) <= 2000


def test_usage_adds() -> None:
    total = Usage(input_tokens=1, output_tokens=2, cache_read_input_tokens=3) + Usage(
        input_tokens=10, output_tokens=20, cache_creation_input_tokens=5
    )
    assert total == Usage(
        input_tokens=11, output_tokens=22, cache_read_input_tokens=3, cache_creation_input_tokens=5
    )
