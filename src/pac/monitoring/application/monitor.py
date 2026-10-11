"""Watch live classifications: running counters, a rolling window, and drift alerts.

The monitor keeps the last ``window`` observations and, every ``check_every`` events once the
window holds ``min_events``, compares them with a reference profile. When a feature's status
changes, it logs once: a WARNING when it drifts (moderate or major), an INFO line when it's
stable again. So a sustained shift produces one alert, not one per event.
"""

from __future__ import annotations

import logging
from collections import Counter, deque
from threading import Lock

from pac.guardrail.domain.classification import Classification, EventClassifier
from pac.guardrail.domain.events import ClassificationRequest
from pac.guardrail.domain.value_object import ValueObject
from pac.monitoring.domain.drift import DriftReport, DriftStatus, Profile, compare
from pac.monitoring.domain.features import Observation, observe

logger = logging.getLogger(__name__)


class Totals(ValueObject):
    """Running counts since the process started (what a /metrics endpoint exposes)."""

    events: int
    by_verdict: dict[str, int]
    by_kind: dict[str, int]
    errors: int
    latency_ms_sum: float


class MonitoringSnapshot(ValueObject):
    totals: Totals
    window_events: int
    window_size: int
    has_reference: bool
    drift: DriftReport | None


class Monitor:
    def __init__(
        self,
        reference: Profile | None,
        *,
        window: int = 500,
        min_events: int = 100,
        check_every: int = 25,
    ) -> None:
        self._reference = reference
        self._window: deque[Observation] = deque(maxlen=window)
        self._min_events = min_events
        self._check_every = check_every
        self._statuses: dict[str, DriftStatus] = {}
        self._last_report: DriftReport | None = None
        self._lock = Lock()
        self._events = 0
        self._errors = 0
        self._latency_ms_sum = 0.0
        self._by_verdict: Counter[str] = Counter()
        self._by_kind: Counter[str] = Counter()

    def record(self, request: ClassificationRequest, result: Classification) -> None:
        observation = observe(request, result)
        with self._lock:
            self._window.append(observation)
            self._events += 1
            self._by_verdict[result.decision.verdict] += 1
            self._by_kind[request.event.kind] += 1
            self._errors += result.error is not None
            self._latency_ms_sum += result.model_latency_ms
            due = (
                self._reference is not None
                and len(self._window) >= self._min_events
                and self._events % self._check_every == 0
            )
        if due:
            self.check()

    def check(self) -> DriftReport | None:
        """Compare the window with the reference now, and alert on any change of status."""
        if self._reference is None:
            return None
        with self._lock:
            current = Profile.of(self._window)
        report = compare(self._reference, current, min_events=self._min_events)
        for feature in report.features:
            before = self._statuses.get(feature.feature, "stable")
            self._statuses[feature.feature] = feature.status
            if feature.status in (before, "insufficient", "not_compared"):
                continue
            if feature.status in ("moderate", "major"):
                logger.warning(
                    "drift detected",
                    extra={
                        "feature": feature.feature,
                        "group": feature.group,
                        "status": feature.status,
                        "psi": feature.psi,
                        "moved": ", ".join(f"{c} {r:.0%}→{n:.0%}" for c, r, n in feature.movers),
                        "new": ",".join(feature.new) or "-",
                    },
                )
            elif before in ("moderate", "major"):
                logger.info(
                    "drift resolved", extra={"feature": feature.feature, "psi": feature.psi}
                )
        self._last_report = report
        return report

    def snapshot(self) -> MonitoringSnapshot:
        report = self.check() if self._reference is not None else None
        with self._lock:
            return MonitoringSnapshot(
                totals=Totals(
                    events=self._events,
                    by_verdict=dict(self._by_verdict),
                    by_kind=dict(self._by_kind),
                    errors=self._errors,
                    latency_ms_sum=round(self._latency_ms_sum, 1),
                ),
                window_events=len(self._window),
                window_size=self._window.maxlen or 0,
                has_reference=self._reference is not None,
                drift=report,
            )


class MonitoredClassifier:
    """A classifier that records every classification with a monitor. Verdicts are unchanged."""

    def __init__(self, inner: EventClassifier, monitor: Monitor) -> None:
        self._inner = inner
        self._monitor = monitor

    @property
    def name(self) -> str:
        return self._inner.name

    @property
    def inner(self) -> EventClassifier:
        return self._inner

    async def classify(self, request: ClassificationRequest) -> Classification:
        result = await self._inner.classify(request)
        try:
            self._monitor.record(request, result)
        except Exception:  # monitoring must never change or break a verdict
            logger.exception("monitoring failed; verdict unaffected")
        return result
