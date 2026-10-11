"""The live monitor: counts every verdict, alerts once on drift, never touches a verdict."""

from __future__ import annotations

import logging

import pytest

from pac.guardrail.domain.classification import Classification
from pac.guardrail.domain.decision import Decision
from pac.guardrail.domain.events import ClassificationRequest, TraceEvent
from pac.monitoring.application.monitor import Monitor, MonitoredClassifier
from pac.monitoring.domain.drift import Profile
from pac.monitoring.domain.features import observe

ALLOW = Classification(
    decision=Decision(rationale="r", verdict="allow", rule_ids=(), confidence=0.97),
    model="m",
    prompt_version="p",
    policy_version="v0",
    latency_ms=1.0,
    model_latency_ms=800.0,
)


def _request(kind: str) -> ClassificationRequest:
    if kind == "tool_call":
        return ClassificationRequest(
            event=TraceEvent(kind="tool_call", tool_name="lookup_order", arguments={"id": 1})
        )
    return ClassificationRequest(event=TraceEvent(kind="user_input", content="hello there"))


class Fixed:
    name = "fixed"

    async def classify(self, request: ClassificationRequest) -> Classification:
        return ALLOW


async def test_records_without_changing_the_verdict() -> None:
    monitor = Monitor(None, window=10)
    classifier = MonitoredClassifier(Fixed(), monitor)
    result = await classifier.classify(_request("user_input"))
    assert result is ALLOW
    snapshot = monitor.snapshot()
    assert snapshot.totals.events == 1
    assert snapshot.totals.by_verdict == {"allow": 1}
    assert snapshot.drift is None
    assert classifier.name == "fixed"


async def test_alerts_once_when_traffic_drifts_then_resolves(
    caplog: pytest.LogCaptureFixture,
) -> None:
    reference = Profile.of(observe(_request("user_input"), ALLOW) for _ in range(100))
    monitor = Monitor(reference, window=100, min_events=50, check_every=10)
    with caplog.at_level(logging.INFO, logger="pac"):
        for _ in range(100):
            monitor.record(_request("tool_call"), ALLOW)
        for _ in range(100):
            monitor.record(_request("user_input"), ALLOW)
    alerts = [r for r in caplog.records if r.getMessage() == "drift detected"]
    kind_alerts = [r for r in alerts if r.feature == "kind"]  # type: ignore[attr-defined]
    assert len(kind_alerts) == 1
    assert kind_alerts[0].levelname == "WARNING"
    assert any(r.getMessage() == "drift resolved" for r in caplog.records)


async def test_a_monitoring_bug_never_breaks_classification(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class Broken(Monitor):
        def record(self, request: ClassificationRequest, result: Classification) -> None:
            raise RuntimeError("bug")

    classifier = MonitoredClassifier(Fixed(), Broken(None))
    with caplog.at_level(logging.ERROR, logger="pac"):
        assert await classifier.classify(_request("user_input")) is ALLOW
    assert any("monitoring failed" in r.getMessage() for r in caplog.records)
