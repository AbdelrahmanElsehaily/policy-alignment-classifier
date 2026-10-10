"""Use cases: run a guardrail over labelled cases, then score and record the run."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from pac.evaluation.application.ports import RunStore
from pac.evaluation.application.reporting import render_report
from pac.evaluation.domain.cases import LabeledCase
from pac.evaluation.domain.prediction import Prediction
from pac.evaluation.domain.run import RunConfig, ScoredRun
from pac.guardrail.domain.classification import EventClassifier

ProgressCallback = Callable[[Prediction], None]


async def run_cases(
    classifier: EventClassifier,
    cases: Sequence[LabeledCase],
    *,
    repeats: int = 1,
    concurrency: int = 4,
    with_history: bool = False,
    on_result: ProgressCallback | None = None,
) -> list[Prediction]:
    """Classify every case ``repeats`` times with bounded concurrency.

    History is stripped unless ``with_history`` is set: the default classifier sees one event,
    as the brief specifies, and history exists in the data only for the ablation.
    """
    if repeats < 1:
        raise ValueError("repeats must be >= 1")
    semaphore = asyncio.Semaphore(concurrency)

    async def one(case: LabeledCase, repeat: int) -> Prediction:
        async with semaphore:
            result = await classifier.classify(case.request_for(with_history=with_history))
        prediction = Prediction.from_case(case, result, repeat, with_history=with_history)
        if on_result is not None:
            on_result(prediction)
        return prediction

    return list(await asyncio.gather(*(one(case, r) for r in range(repeats) for case in cases)))


def new_run_id(classifier_name: str, split: str, *, history: bool = False) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    slug = re.sub(r"[^a-z0-9.]+", "-", classifier_name.lower()).strip("-")
    return f"{stamp}_{slug}_{split}{'_history' if history else ''}"


def record_run(store: RunStore, config: RunConfig, predictions: Sequence[Prediction]) -> ScoredRun:
    """Score a run and persist it with its report."""
    run = ScoredRun.score(config, predictions)
    store.save(run, render_report(run))
    return run


def rescore_run(store: RunStore, run_id: str) -> ScoredRun:
    """Recompute metrics and report from a run's saved predictions (e.g. after a metrics fix)."""
    saved = store.load(run_id)
    return record_run(store, saved.config, saved.predictions)
