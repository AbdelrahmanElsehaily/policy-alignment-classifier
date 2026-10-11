"""Monitoring features and drift: what is measured, and when a shift counts as drift."""

from __future__ import annotations

import pytest

from pac.guardrail.domain.classification import Classification
from pac.guardrail.domain.decision import Decision, Verdict
from pac.guardrail.domain.events import ClassificationRequest, Session, TraceEvent
from pac.monitoring.domain.drift import MAJOR_PSI, Profile, compare, psi, smoothed_shares
from pac.monitoring.domain.features import (
    MODEL_FEATURES,
    Observation,
    bucket,
    observe,
    observe_expected,
    script_of,
)


def _result(verdict: Verdict = "allow", *, error: str | None = None) -> Classification:
    rules = ("R4",) if verdict == "violation" else ()
    return Classification(
        decision=Decision(rationale="r", verdict=verdict, rule_ids=rules, confidence=0.97),
        model="m",
        prompt_version="p",
        policy_version="v0",
        latency_ms=1.0,
        model_latency_ms=1200.0,
        error=error,
    )


def _obs(**values: str) -> Observation:
    return Observation(values={k: (v,) for k, v in values.items()})


@pytest.mark.parametrize(
    ("text", "script"),
    [
        ("Where is my order?", "latin"),
        ("مرحبا، متى يصل طلبي؟", "arabic"),
        ("Hola, ¿cuándo llega?", "latin"),
        ("12345 !!", "none"),
        ("hello مرحبا", "mixed"),
    ],
)
def test_script_detection(text: str, script: str) -> None:
    assert script_of(text) == script


def test_buckets() -> None:
    assert bucket(10, (50, 150), ("<50", "50-150", "150+")) == "<50"
    assert bucket(150, (50, 150), ("<50", "50-150", "150+")) == "150+"


def test_observe_covers_input_and_output() -> None:
    request = ClassificationRequest(
        event=TraceEvent(kind="tool_call", tool_name="issue_refund", arguments={"amount": 30}),
        session=Session(verified=True, verified_customer_id="C-1", email_on_file="a@b.c"),
        history=(TraceEvent(kind="user_input", content="refund please"),),
    )
    values = observe(request, _result("violation")).values
    assert values["kind"] == ("tool_call",)
    assert values["tool"] == ("issue_refund",)
    assert values["verified"] == ("verified",)
    assert values["history"] == ("1-2",)
    assert values["verdict"] == ("violation",)
    assert values["gate"] == ("block",)
    assert values["rules"] == ("R4",)
    assert values["confidence"] == ("0.95+",)
    assert values["latency"] == ("0.5-2s",)
    assert observe(request, _result("allow", error="x")).values["error"] == ("error",)


def test_identical_traffic_is_stable() -> None:
    profile = Profile.of([_obs(kind="user_input")] * 60 + [_obs(kind="tool_call")] * 60)
    report = compare(profile, profile)
    kind = next(f for f in report.features if f.feature == "kind")
    assert kind.psi == 0.0
    assert kind.status == "stable"


def test_a_big_shift_is_major_and_names_what_moved() -> None:
    reference = Profile.of([_obs(kind="user_input")] * 90 + [_obs(kind="tool_call")] * 30)
    current = Profile.of([_obs(kind="user_input")] * 30 + [_obs(kind="tool_call")] * 90)
    kind = next(f for f in compare(reference, current).features if f.feature == "kind")
    assert kind.psi is not None
    assert kind.psi >= MAJOR_PSI
    assert kind.status == "major"
    assert kind.movers[0][0] in {"user_input", "tool_call"}


def test_rare_categories_dont_raise_false_alarms() -> None:
    # A tool seen once in 120 events, then not at all: without merging, PSI would explode.
    reference = Profile.of([_obs(tool="lookup_order")] * 119 + [_obs(tool="cancel_order")])
    current = Profile.of([_obs(tool="lookup_order")] * 120)
    tool = next(f for f in compare(reference, current).features if f.feature == "tool")
    assert tool.status == "stable"


def test_a_new_category_is_reported_even_when_psi_is_small() -> None:
    reference = Profile.of([_obs(script="latin")] * 200)
    current = Profile.of([_obs(script="latin")] * 194 + [_obs(script="arabic")] * 6)
    script = next(f for f in compare(reference, current).features if f.feature == "script")
    assert script.new == ("arabic",)
    assert script.status == "moderate"


def test_too_little_data_is_not_judged() -> None:
    small = Profile.of([_obs(kind="user_input")] * 10)
    report = compare(small, small, min_events=100)
    assert report.status == "insufficient"
    assert all(f.psi is None for f in report.features)


def test_psi_and_smoothing() -> None:
    assert psi({"a": 0.5, "b": 0.5}, {"a": 0.5, "b": 0.5}) == 0.0
    ref, cur = smoothed_shares({"a": 10}, {"a": 5, "b": 5})
    assert set(ref) == set(cur) == {"a", "b"}
    assert sum(ref.values()) == pytest.approx(1.0)
    assert ref["b"] > 0


def test_an_expected_answer_has_no_model_features() -> None:
    request = ClassificationRequest(event=TraceEvent(kind="user_input", content="hi"))
    expected = observe_expected(request, "violation", ("R13",))
    assert expected.values["verdict"] == ("violation",)
    assert expected.values["gate"] == ("block",)
    assert expected.values["rules"] == ("R13",)
    assert not set(MODEL_FEATURES) & set(expected.values)


def test_features_the_reference_lacks_are_not_compared() -> None:
    reference = Profile.of([_obs(verdict="allow")] * 200)
    current = Profile.of([_obs(verdict="allow", latency="0.5-2s")] * 200)
    report = compare(reference, current)
    by_name = {f.feature: f.status for f in report.features}
    assert by_name["latency"] == "not_compared"
    assert by_name["verdict"] == "stable"
    assert report.status == "stable"
