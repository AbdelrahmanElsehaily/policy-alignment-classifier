from __future__ import annotations

import pytest

from pac.eval.metrics import (
    bootstrap_macro_f1,
    compute_metrics,
    confusion,
    flip_rate,
    macro_f1,
    percentile,
    wilson,
)
from pac.eval.records import Prediction
from pac.schemas import Classification, Decision, EventKind, Usage, Verdict


def pred(
    case_id: str,
    gold: Verdict,
    predicted: Verdict,
    *,
    kind: EventKind = "tool_call",
    gold_rules: tuple[str, ...] = (),
    rules: tuple[str, ...] = (),
    confidence: float = 0.9,
    repeat: int = 0,
    error: str | None = None,
    model: str = "claude-haiku-5-5",
    latency: float = 100.0,
) -> Prediction:
    if predicted == "violation" and not rules:
        rules = ("R1",)
    return Prediction(
        case_id=case_id,
        repeat=repeat,
        split="test",
        kind=kind,
        gold=gold,
        gold_rules=gold_rules or (("R1",) if gold == "violation" else ()),
        label_confidence="high",
        tags=("t",),
        origin="targeted",
        result=Classification(
            decision=Decision(
                rationale="r", verdict=predicted, rule_ids=rules, confidence=confidence
            ),
            model=model,
            prompt_version="p",
            policy_version="v0",
            latency_ms=1.0,
            model_latency_ms=latency,
            usage=Usage(input_tokens=1000, output_tokens=100),
            error=error,
        ),
    )


def test_wilson_known_values() -> None:
    r = wilson(8, 10)
    assert r.value == pytest.approx(0.8)
    assert r.ci_low == pytest.approx(0.4902, abs=1e-3)
    assert r.ci_high == pytest.approx(0.9433, abs=1e-3)
    assert wilson(0, 0).value is None
    assert wilson(0, 5).ci_low == 0.0
    assert wilson(5, 5).ci_high == 1.0


def test_percentile_interpolates() -> None:
    assert percentile([1, 2, 3, 4], 0.5) == pytest.approx(2.5)
    assert percentile([], 0.5) is None


def test_confusion_and_macro_f1() -> None:
    preds = [
        pred("a", "violation", "violation"),
        pred("b", "violation", "allow"),
        pred("c", "allow", "allow"),
        pred("d", "allow", "violation"),
    ]
    matrix = confusion(preds)
    assert matrix["violation"] == {"allow": 1, "violation": 1, "policy_silent": 0, "error": 0}
    assert matrix["allow"] == {"allow": 1, "violation": 1, "policy_silent": 0, "error": 0}
    # Both classes have precision = recall = 0.5; policy_silent has no support so is excluded.
    assert macro_f1(preds) == pytest.approx(0.5)


def test_bootstrap_is_seeded_and_brackets_point_estimate() -> None:
    preds = [pred(str(i), "allow" if i % 3 else "violation", "allow") for i in range(30)]
    ci = bootstrap_macro_f1(preds, seed=1)
    assert ci == bootstrap_macro_f1(preds, seed=1)
    point = macro_f1(preds)
    assert ci is not None
    assert point is not None
    assert ci[0] <= point <= ci[1]


def test_guardrail_rates() -> None:
    preds = [
        pred("v1", "violation", "violation", gold_rules=("R5",), rules=("R5",)),
        pred("v2", "violation", "violation", gold_rules=("R5",), rules=("R7",)),
        pred("v3", "violation", "policy_silent"),
        pred("v4", "violation", "allow"),
        pred("a1", "allow", "allow"),
        pred("a2", "allow", "violation"),
        pred("a3", "allow", "policy_silent"),
        pred("s1", "policy_silent", "policy_silent"),
    ]
    m = compute_metrics(preds)
    assert (m.violation_recall.k, m.violation_recall.n) == (2, 4)
    assert m.violation_caught_or_escalated.k == 3
    assert (m.violation_precision.k, m.violation_precision.n) == (2, 3)
    assert (m.false_block_rate.k, m.false_block_rate.n) == (1, 3)
    assert m.escalation_rate_on_allow.k == 1
    assert (m.silent_recall.k, m.silent_recall.n) == (1, 1)
    assert (m.rule_attribution.k, m.rule_attribution.n) == (1, 2)


def test_repeats_excluded_from_headline_but_used_for_flips() -> None:
    preds = [
        pred("a", "allow", "allow", repeat=0),
        pred("a", "allow", "violation", repeat=1),
        pred("b", "allow", "allow", repeat=0),
        pred("b", "allow", "allow", repeat=1),
    ]
    m = compute_metrics(preds)
    assert (m.n_cases, m.repeats, m.accuracy.k) == (2, 2, 2)
    flips = flip_rate(preds)
    assert flips is not None
    assert (flips.k, flips.n) == (1, 2)
    assert flip_rate(preds[:1]) is None


def test_breakdowns_and_operational() -> None:
    preds = [
        pred("a", "allow", "allow", kind="user_input", latency=100),
        pred("b", "allow", "violation", kind="model_output", latency=300),
        pred("c", "violation", "policy_silent", error="timed out", confidence=0.0),
    ]
    m = compute_metrics(preds)
    assert set(m.by_kind) == {"user_input", "model_output", "tool_call"}
    assert m.by_kind["model_output"].false_block_rate.k == 1
    assert m.by_tag["t"].n == 3
    assert m.operational.classifier_errors.k == 1
    assert m.operational.model_latency_p50_ms == pytest.approx(100)
    # Haiku 5.5: 1000 in x $0.10/M + 100 out x $0.50/M = $0.00015 per event
    assert m.operational.cost_per_1k_events_usd == pytest.approx(0.15)


def test_cost_unknown_when_any_model_unpriced() -> None:
    m = compute_metrics([pred("a", "allow", "allow", model="MiniMax-M3")])
    assert m.operational.cost_per_1k_events_usd is None


def test_calibration_excludes_errors() -> None:
    preds = [
        pred("a", "allow", "allow", confidence=0.95),
        pred("b", "allow", "violation", confidence=0.95),
        pred("c", "allow", "policy_silent", confidence=0.0, error="boom"),
    ]
    m = compute_metrics(preds)
    top = m.calibration[-1]
    assert top.n == 2
    assert top.accuracy == pytest.approx(0.5)
    assert m.expected_calibration_error == pytest.approx(0.45)


def test_metrics_do_not_depend_on_prediction_order() -> None:
    preds = [pred(str(i), "allow" if i % 3 else "violation", "allow") for i in range(20)]
    assert compute_metrics(preds) == compute_metrics(list(reversed(preds)))


def test_errors_are_never_scored_as_correct_silence() -> None:
    preds = [
        pred("s1", "policy_silent", "policy_silent", error="timed out", confidence=0.0),
        pred("s2", "policy_silent", "policy_silent"),
        pred("v1", "violation", "policy_silent", error="boom", confidence=0.0),
        pred("a1", "allow", "policy_silent", error="boom", confidence=0.0),
    ]
    m = compute_metrics(preds)
    assert (m.accuracy.k, m.accuracy.n) == (1, 4)
    assert (m.silent_recall.k, m.silent_recall.n) == (1, 2)
    assert m.confusion["policy_silent"]["error"] == 1
    assert m.per_verdict["policy_silent"].precision == 1.0
    # Operationally, a fail-closed error still routes the event to a human.
    assert m.violation_caught_or_escalated.k == 1
    assert m.escalation_rate_on_allow.k == 1
