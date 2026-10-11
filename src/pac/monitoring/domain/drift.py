"""Distributions of features, and how far one has moved from another (PSI).

The Population Stability Index compares two category distributions:
``PSI = Σ (current - reference) · ln(current / reference)`` over categories. The usual reading,
used here: below 0.1 stable, 0.1 to 0.25 a moderate shift worth a look, 0.25 and above a major
shift. Below ``min_events`` in either distribution there is too little data to judge, and the
report says so instead of guessing.

PSI is unstable on rare categories with small samples: one event of a rare tool can swing it
past 0.25. Two standard remedies keep the alerts meaningful: categories under ``RARE_SHARE`` in
both distributions are merged into ``other``, and counts are Laplace-smoothed (half an event
per category) so a category moving from 0 to 1 occurrence isn't treated as near-infinite change.

PSI can also miss what matters most: a category the reference never saw (a new language, tool or
event kind) at a few percent barely moves it. So any such category reaching ``RARE_SHARE`` of
current traffic makes the feature at least ``moderate`` and is named in the report.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Literal

from pydantic import Field

from pac.guardrail.domain.value_object import ValueObject
from pac.monitoring.domain.features import FEATURE_GROUP, FeatureGroup, Observation

DriftStatus = Literal["stable", "moderate", "major", "insufficient"]

MODERATE_PSI = 0.1
MAJOR_PSI = 0.25
RARE_SHARE = 0.02
_PSEUDOCOUNT = 0.5


class Profile(ValueObject):
    """Category counts per feature over a set of events."""

    events: int = 0
    counts: dict[str, dict[str, int]] = Field(default_factory=dict)

    @classmethod
    def of(cls, observations: Iterable[Observation]) -> Profile:
        counts: dict[str, Counter[str]] = {}
        events = 0
        for observation in observations:
            events += 1
            for feature, categories in observation.values.items():
                counts.setdefault(feature, Counter()).update(categories)
        return cls(events=events, counts={f: dict(c) for f, c in counts.items()})

    def shares(self, feature: str) -> dict[str, float]:
        counts = self.counts.get(feature, {})
        total = sum(counts.values())
        return {k: v / total for k, v in counts.items()} if total else {}


def psi(reference: Mapping[str, float], current: Mapping[str, float]) -> float:
    """Population Stability Index between two category distributions with no empty categories."""
    return sum(
        (current[c] - reference[c]) * math.log(current[c] / reference[c])
        for c in set(reference) & set(current)
    )


def smoothed_shares(
    reference: Mapping[str, int], current: Mapping[str, int]
) -> tuple[dict[str, float], dict[str, float]]:
    """Shares over the union of categories, with rare ones merged and counts smoothed."""
    ref_total, cur_total = sum(reference.values()) or 1, sum(current.values()) or 1
    merged: dict[str, list[float]] = {}
    for category in set(reference) | set(current):
        r, c = reference.get(category, 0), current.get(category, 0)
        rare = r / ref_total < RARE_SHARE and c / cur_total < RARE_SHARE
        pair = merged.setdefault("other" if rare else category, [0.0, 0.0])
        pair[0] += r
        pair[1] += c
    pseudo = _PSEUDOCOUNT * len(merged)
    ref_sum = sum(r for r, _ in merged.values()) + pseudo
    cur_sum = sum(c for _, c in merged.values()) + pseudo
    return (
        {k: (r + _PSEUDOCOUNT) / ref_sum for k, (r, _) in merged.items()},
        {k: (c + _PSEUDOCOUNT) / cur_sum for k, (_, c) in merged.items()},
    )


def status_of(value: float) -> DriftStatus:
    if value >= MAJOR_PSI:
        return "major"
    if value >= MODERATE_PSI:
        return "moderate"
    return "stable"


class FeatureDrift(ValueObject):
    feature: str
    group: FeatureGroup
    psi: float | None
    status: DriftStatus
    # The categories that moved most, as (category, reference share, current share).
    movers: tuple[tuple[str, float, float], ...] = ()
    # Categories the reference never saw that are now at least RARE_SHARE of traffic.
    new: tuple[str, ...] = ()


class DriftReport(ValueObject):
    reference_events: int
    current_events: int
    status: DriftStatus
    features: tuple[FeatureDrift, ...]

    def drifting(self) -> tuple[FeatureDrift, ...]:
        return tuple(f for f in self.features if f.status in ("moderate", "major"))


def compare(reference: Profile, current: Profile, *, min_events: int = 100) -> DriftReport:
    """How far ``current`` has moved from ``reference``, feature by feature."""
    enough = reference.events >= min_events and current.events >= min_events
    features = []
    for feature, group in FEATURE_GROUP.items():
        ref, cur = reference.shares(feature), current.shares(feature)
        if not enough or not ref or not cur:
            features.append(
                FeatureDrift(feature=feature, group=group, psi=None, status="insufficient")
            )
            continue
        value = psi(*smoothed_shares(reference.counts[feature], current.counts[feature]))
        new = tuple(sorted(c for c, share in cur.items() if c not in ref and share >= RARE_SHARE))
        status = status_of(value)
        if new and status == "stable":
            status = "moderate"
        movers = sorted(
            set(ref) | set(cur), key=lambda c: abs(cur.get(c, 0.0) - ref.get(c, 0.0)), reverse=True
        )[:3]
        features.append(
            FeatureDrift(
                feature=feature,
                group=group,
                psi=round(value, 4),
                status=status,
                movers=tuple(
                    (c, round(ref.get(c, 0.0), 4), round(cur.get(c, 0.0), 4)) for c in movers
                ),
                new=new,
            )
        )
    statuses = {f.status for f in features}
    overall: DriftStatus = (
        "insufficient"
        if statuses == {"insufficient"}
        else "major"
        if "major" in statuses
        else "moderate"
        if "moderate" in statuses
        else "stable"
    )
    return DriftReport(
        reference_events=reference.events,
        current_events=current.events,
        status=overall,
        features=tuple(features),
    )
