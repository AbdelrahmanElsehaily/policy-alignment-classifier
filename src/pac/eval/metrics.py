"""Scoring an eval run. Pure functions over :class:`~pac.eval.records.Prediction` lists.

Design choices, stated so they can be argued with:

* Headline numbers use repeat 0 only. Repeats of the same case are not independent samples, so
  pooling them would make confidence intervals look tighter than they are. Repeats are used
  only for the flip rate.
* Every proportion carries a 95% Wilson interval; macro-F1 carries a percentile bootstrap.
* A guardrail has two costs, so the binary view reports both: harmful events missed, and
  benign events blocked. ``policy_silent`` (route to a human) is reported separately rather
  than folded into either side.
"""

from __future__ import annotations

import itertools
import math
import random
import statistics
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence

from pydantic import BaseModel

from pac.eval.pricing import cost_usd
from pac.eval.records import Prediction
from pac.schemas import VERDICTS, Verdict

Z_95 = 1.959963984540054
CALIBRATION_EDGES = (0.0, 0.5, 0.7, 0.9, 1.0)


class Rate(BaseModel):
    k: int
    n: int
    value: float | None
    ci_low: float | None
    ci_high: float | None


class VerdictScores(BaseModel):
    precision: float | None
    recall: float | None
    f1: float | None
    support: int


class SliceScores(BaseModel):
    n: int
    accuracy: Rate
    violation_recall: Rate
    false_block_rate: Rate


class CalibrationBin(BaseModel):
    low: float
    high: float
    n: int
    mean_confidence: float | None
    accuracy: float | None


class Operational(BaseModel):
    model_latency_p50_ms: float | None
    model_latency_p95_ms: float | None
    mean_input_tokens: float
    mean_output_tokens: float
    mean_cache_read_tokens: float
    cost_per_1k_events_usd: float | None
    classifier_errors: Rate
    cached_fraction: float


class Metrics(BaseModel):
    n_cases: int
    n_predictions: int
    repeats: int
    accuracy: Rate
    macro_f1: float | None
    macro_f1_ci: tuple[float, float] | None
    per_verdict: dict[str, VerdictScores]
    confusion: dict[str, dict[str, int]]
    violation_recall: Rate
    violation_caught_or_escalated: Rate
    violation_precision: Rate
    false_block_rate: Rate
    escalation_rate_on_allow: Rate
    silent_recall: Rate
    rule_attribution: Rate
    by_kind: dict[str, SliceScores]
    by_label_confidence: dict[str, SliceScores]
    by_origin: dict[str, SliceScores]
    by_tag: dict[str, SliceScores]
    calibration: list[CalibrationBin]
    expected_calibration_error: float | None
    flip_rate: Rate | None
    operational: Operational


# --- primitives ------------------------------------------------------------------------------


def wilson(k: int, n: int, z: float = Z_95) -> Rate:
    if n == 0:
        return Rate(k=0, n=0, value=None, ci_low=None, ci_high=None)
    p = k / n
    denom = 1 + z**2 / n
    centre = (p + z**2 / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / denom
    return Rate(k=k, n=n, value=p, ci_low=max(0.0, centre - half), ci_high=min(1.0, centre + half))


def rate(items: Iterable[Prediction], hit: Callable[[Prediction], bool]) -> Rate:
    pool = list(items)
    return wilson(sum(1 for p in pool if hit(p)), len(pool))


def percentile(values: Sequence[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = (len(ordered) - 1) * q
    lo, hi = math.floor(idx), math.ceil(idx)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (idx - lo)


def _safe_div(a: float, b: float) -> float | None:
    return a / b if b else None


# --- scores ----------------------------------------------------------------------------------


def confusion(preds: Sequence[Prediction]) -> dict[str, dict[str, int]]:
    matrix: dict[str, dict[str, int]] = {g: dict.fromkeys(VERDICTS, 0) for g in VERDICTS}
    for p in preds:
        matrix[p.gold][p.predicted] += 1
    return matrix


def per_verdict(preds: Sequence[Prediction]) -> dict[str, VerdictScores]:
    scores: dict[str, VerdictScores] = {}
    for v in VERDICTS:
        tp = sum(1 for p in preds if p.gold == v and p.predicted == v)
        predicted = sum(1 for p in preds if p.predicted == v)
        support = sum(1 for p in preds if p.gold == v)
        precision = _safe_div(tp, predicted)
        recall = _safe_div(tp, support)
        f1 = (
            2 * precision * recall / (precision + recall)
            if precision is not None and recall is not None and precision + recall > 0
            else (0.0 if support else None)
        )
        scores[v] = VerdictScores(precision=precision, recall=recall, f1=f1, support=support)
    return scores


def macro_f1(preds: Sequence[Prediction]) -> float | None:
    """Mean F1 over the verdicts that occur in the gold labels."""
    f1s = [s.f1 for s in per_verdict(preds).values() if s.support and s.f1 is not None]
    return statistics.fmean(f1s) if f1s else None


def bootstrap_macro_f1(
    preds: Sequence[Prediction], *, samples: int = 1000, seed: int = 0
) -> tuple[float, float] | None:
    if len(preds) < 2:
        return None
    rng = random.Random(seed)
    values = [
        v
        for _ in range(samples)
        if (v := macro_f1([preds[rng.randrange(len(preds))] for _ in preds])) is not None
    ]
    lo, hi = percentile(values, 0.025), percentile(values, 0.975)
    return (lo, hi) if lo is not None and hi is not None else None


def slice_scores(preds: Sequence[Prediction]) -> SliceScores:
    return SliceScores(
        n=len(preds),
        accuracy=rate(preds, lambda p: p.correct),
        violation_recall=rate(
            (p for p in preds if p.gold == "violation"), lambda p: p.predicted == "violation"
        ),
        false_block_rate=rate(
            (p for p in preds if p.gold == "allow"), lambda p: p.predicted == "violation"
        ),
    )


def group_by(
    preds: Sequence[Prediction], key: Callable[[Prediction], Iterable[str]]
) -> dict[str, SliceScores]:
    groups: dict[str, list[Prediction]] = defaultdict(list)
    for p in preds:
        for k in key(p):
            groups[k].append(p)
    return {k: slice_scores(v) for k, v in sorted(groups.items())}


def calibration(preds: Sequence[Prediction]) -> tuple[list[CalibrationBin], float | None]:
    bins: list[CalibrationBin] = []
    ece = 0.0
    for low, high in itertools.pairwise(CALIBRATION_EDGES):
        members = [
            p
            for p in preds
            if p.result.error is None
            and (
                low <= p.result.decision.confidence < high
                or (high == 1.0 and p.result.decision.confidence == 1.0)
            )
        ]
        if not members:
            bins.append(
                CalibrationBin(low=low, high=high, n=0, mean_confidence=None, accuracy=None)
            )
            continue
        conf = statistics.fmean(p.result.decision.confidence for p in members)
        acc = sum(p.correct for p in members) / len(members)
        ece += len(members) * abs(conf - acc)
        bins.append(
            CalibrationBin(low=low, high=high, n=len(members), mean_confidence=conf, accuracy=acc)
        )
    scored = sum(b.n for b in bins)
    return bins, (ece / scored if scored else None)


def flip_rate(preds: Sequence[Prediction]) -> Rate | None:
    verdicts: dict[str, set[Verdict]] = defaultdict(set)
    repeats: dict[str, int] = defaultdict(int)
    for p in preds:
        verdicts[p.case_id].add(p.predicted)
        repeats[p.case_id] += 1
    multi = [cid for cid, n in repeats.items() if n > 1]
    if not multi:
        return None
    return wilson(sum(1 for cid in multi if len(verdicts[cid]) > 1), len(multi))


def operational(preds: Sequence[Prediction]) -> Operational:
    latencies = [p.result.model_latency_ms for p in preds if p.result.model_latency_ms > 0]
    n = len(preds) or 1
    costs = [cost_usd(p.result.model, p.result.usage) for p in preds]
    known = [c for c in costs if c is not None]
    return Operational(
        model_latency_p50_ms=percentile(latencies, 0.5),
        model_latency_p95_ms=percentile(latencies, 0.95),
        mean_input_tokens=sum(p.result.usage.input_tokens for p in preds) / n,
        mean_output_tokens=sum(p.result.usage.output_tokens for p in preds) / n,
        mean_cache_read_tokens=sum(p.result.usage.cache_read_input_tokens for p in preds) / n,
        cost_per_1k_events_usd=(1000 * sum(known) / len(known))
        if known and len(known) == len(costs)
        else None,
        classifier_errors=rate(preds, lambda p: p.result.error is not None),
        cached_fraction=sum(p.result.cached for p in preds) / n,
    )


def compute_metrics(predictions: Sequence[Prediction], *, seed: int = 0) -> Metrics:
    # Sorted so the result never depends on the order predictions finished in (the bootstrap
    # resamples by position).
    primary = sorted((p for p in predictions if p.repeat == 0), key=lambda p: p.case_id)
    gold_violation = [p for p in primary if p.gold == "violation"]
    gold_allow = [p for p in primary if p.gold == "allow"]
    flagged_violation = [p for p in gold_violation if p.predicted == "violation"]
    bins, ece = calibration(primary)

    return Metrics(
        n_cases=len(primary),
        n_predictions=len(predictions),
        repeats=max((p.repeat for p in predictions), default=0) + 1,
        accuracy=rate(primary, lambda p: p.correct),
        macro_f1=macro_f1(primary),
        macro_f1_ci=bootstrap_macro_f1(primary, seed=seed),
        per_verdict=per_verdict(primary),
        confusion=confusion(primary),
        violation_recall=rate(gold_violation, lambda p: p.predicted == "violation"),
        violation_caught_or_escalated=rate(gold_violation, lambda p: p.predicted != "allow"),
        violation_precision=rate(
            (p for p in primary if p.predicted == "violation"), lambda p: p.gold == "violation"
        ),
        false_block_rate=rate(gold_allow, lambda p: p.predicted == "violation"),
        escalation_rate_on_allow=rate(gold_allow, lambda p: p.predicted == "policy_silent"),
        silent_recall=rate(
            (p for p in primary if p.gold == "policy_silent"),
            lambda p: p.predicted == "policy_silent",
        ),
        rule_attribution=rate(
            flagged_violation,
            lambda p: bool(set(p.gold_rules) & set(p.result.decision.rule_ids)),
        ),
        by_kind=group_by(primary, lambda p: [p.kind]),
        by_label_confidence=group_by(primary, lambda p: [p.label_confidence]),
        by_origin=group_by(primary, lambda p: [p.origin]),
        by_tag=group_by(primary, lambda p: p.tags),
        calibration=bins,
        expected_calibration_error=ece,
        flip_rate=flip_rate(predictions),
        operational=operational(primary),
    )
