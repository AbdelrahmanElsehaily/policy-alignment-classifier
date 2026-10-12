"""Use case: calibrate the labelling pipeline against existing gold labels (guideline 5).

The check set is the 49 prompt-development events (``dev``) and the 257 generated train events,
whose labels were written before any of this existed. The test split, the stress set and the
agents set are never used here. Each iteration labels the check set with one version of the
jury guide and labelling functions and reports verdict agreement and Cohen's kappa against
gold, for the pipeline, for each juror alone, and for the labelling functions where they fire.

Gold here is Claude's labels, and one juror is Claude: agreement with gold measures, in part,
one model family agreeing with itself. Per-family numbers make that visible.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Callable, Sequence
from typing import Any

from pac.guardrail.domain.decision import VERDICTS, Verdict
from pac.guardrail.domain.events import ClassificationRequest
from pac.guardrail.domain.value_object import ValueObject
from pac.synthesis.application.labeller import JuryLabeller
from pac.synthesis.domain.jury import Consensus, aggregate, juror_verdict
from pac.synthesis.domain.labelling import labelling_functions
from pac.synthesis.domain.metrics import agreement, cohen_kappa, confusion, fleiss_kappa
from pac.synthesis.domain.specs import rules_for


class CheckCase(ValueObject):
    id: str
    source: str  # "dev" or "train"
    request: ClassificationRequest
    gold: Verdict
    gold_rules: tuple[str, ...] = ()
    gold_confidence: str = "high"
    template: str = ""


class CaseResult(ValueObject):
    id: str
    source: str
    kind: str
    gold: Verdict
    predicted: Verdict
    rule_ids: tuple[str, ...]
    confidence: str
    juror_verdicts: dict[str, Verdict]  # with labelling functions applied
    juror_raw: dict[str, Verdict]  # the juror alone, no labelling functions
    lf_rules: dict[str, str]
    rationale: str


def _summary(gold: Sequence[str], predicted: Sequence[str]) -> dict[str, Any]:
    return {
        "n": len(gold),
        "agreement": round(agreement(gold, predicted), 4),
        "kappa": round(cohen_kappa(gold, predicted), 4),
    }


async def label_check_set(
    labeller: JuryLabeller,
    cases: Sequence[CheckCase],
    *,
    concurrency: int = 4,
    progress: Callable[[int], None] | None = None,
) -> list[CaseResult]:
    semaphore = asyncio.Semaphore(concurrency)
    done = 0

    async def one(case: CheckCase) -> CaseResult:
        nonlocal done
        async with semaphore:
            request = case.request.without_history()
            ballots = await labeller.ballots(request)
        rules = rules_for(request.event.kind, labeller.applies_to)
        lfs = labelling_functions(request)
        consensus: Consensus = aggregate(ballots, rules, lfs)
        done += 1
        if progress is not None:
            progress(done)
        return CaseResult(
            id=case.id,
            source=case.source,
            kind=request.event.kind,
            gold=case.gold,
            predicted=consensus.verdict,
            rule_ids=consensus.rule_ids,
            confidence=consensus.confidence,
            juror_verdicts=consensus.juror_verdicts,
            juror_raw={b.juror: juror_verdict(b, rules, {}) for b in ballots if b.valid},
            lf_rules={r: lf.verdict for r, lf in lfs.items()},
            rationale=consensus.rationale,
        )

    return list(await asyncio.gather(*(one(c) for c in cases)))


def report(results: Sequence[CaseResult], jurors: Sequence[str]) -> dict[str, Any]:
    """Agreement and kappa: overall, by source, by kind, by juror, and the jury's own kappa."""
    gold = [r.gold for r in results]
    pred = [r.predicted for r in results]
    out: dict[str, Any] = {"pipeline": _summary(gold, pred)}
    for key in ("source", "kind"):
        groups: dict[str, list[CaseResult]] = {}
        for r in results:
            groups.setdefault(getattr(r, key), []).append(r)
        out[f"by_{key}"] = {
            g: _summary([r.gold for r in rs], [r.predicted for r in rs])
            for g, rs in sorted(groups.items())
        }
    per_juror: dict[str, Any] = {}
    for juror in jurors:
        with_lf = [(r.gold, r.juror_verdicts[juror]) for r in results if juror in r.juror_verdicts]
        alone = [(r.gold, r.juror_raw[juror]) for r in results if juror in r.juror_raw]
        per_juror[juror] = {
            "with_lfs": _summary([g for g, _ in with_lf], [p for _, p in with_lf])
            if with_lf
            else None,
            "alone": _summary([g for g, _ in alone], [p for _, p in alone]) if alone else None,
            "missing": len(results) - len(alone),
        }
    out["by_juror"] = per_juror
    full = [r for r in results if all(j in r.juror_raw for j in jurors)]
    out["jury_fleiss_kappa_alone"] = round(
        fleiss_kappa([[r.juror_raw[j] for j in jurors] for r in full]), 4
    )
    lf_fired = [r for r in results if r.lf_rules]
    out["lf_fired"] = len(lf_fired)
    out["confusion"] = confusion(gold, pred, list(VERDICTS))
    out["by_confidence"] = {
        c: _summary(
            [r.gold for r in results if r.confidence == c],
            [r.predicted for r in results if r.confidence == c],
        )
        for c in ("high", "medium", "contested")
        if any(r.confidence == c for r in results)
    }
    out["gold_mix"] = dict(Counter(gold))
    out["predicted_mix"] = dict(Counter(pred))
    return out


def disagreements(results: Sequence[CaseResult]) -> list[CaseResult]:
    return [r for r in results if r.gold != r.predicted]
