"""Running a classifier over the eval set and persisting the run."""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from pac.classifier import EventClassifier
from pac.eval.metrics import Metrics, compute_metrics
from pac.eval.records import Prediction, RunConfig
from pac.eval.report import render_report
from pac.schemas import LabeledCase

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
        request = case.request if with_history else case.request.model_copy(update={"history": ()})
        async with semaphore:
            result = await classifier.classify(request)
        prediction = Prediction.from_case(case, result, repeat, with_history=with_history)
        if on_result is not None:
            on_result(prediction)
        return prediction

    return list(await asyncio.gather(*(one(case, r) for r in range(repeats) for case in cases)))


def new_run_id(classifier_name: str, split: str, *, history: bool = False) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    slug = re.sub(r"[^a-z0-9.]+", "-", classifier_name.lower()).strip("-")
    return f"{stamp}_{slug}_{split}{'_history' if history else ''}"


def git_commit() -> str | None:
    """Short HEAD sha, suffixed ``-dirty`` when tracked files have uncommitted changes."""
    try:
        head = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "diff", "--quiet", "HEAD", "--"], capture_output=True, timeout=5, check=False
        ).returncode
    except (OSError, subprocess.SubprocessError):
        return None
    if not head:
        return None
    return f"{head}-dirty" if dirty == 1 else head


def write_run(run_dir: Path, config: RunConfig, predictions: Sequence[Prediction]) -> Metrics:
    """Persist a run: config, raw predictions, metrics, and a human-readable report."""
    run_dir.mkdir(parents=True, exist_ok=True)
    metrics = compute_metrics(predictions)
    ordered = sorted(predictions, key=lambda p: (p.case_id, p.repeat))
    (run_dir / "config.json").write_text(config.model_dump_json(indent=2) + "\n")
    (run_dir / "predictions.jsonl").write_text("".join(p.model_dump_json() + "\n" for p in ordered))
    (run_dir / "metrics.json").write_text(metrics.model_dump_json(indent=2) + "\n")
    (run_dir / "report.md").write_text(render_report(config, metrics, ordered))
    return metrics


def load_run(run_dir: Path) -> tuple[RunConfig, list[Prediction], Metrics]:
    config = RunConfig.model_validate_json((run_dir / "config.json").read_text())
    predictions = [
        Prediction.model_validate_json(line)
        for line in (run_dir / "predictions.jsonl").read_text().splitlines()
        if line.strip()
    ]
    metrics = Metrics.model_validate(json.loads((run_dir / "metrics.json").read_text()))
    return config, predictions, metrics


def list_runs(results_dir: Path) -> list[Path]:
    runs_dir = results_dir / "runs"
    if not runs_dir.is_dir():
        return []
    return sorted(
        (p for p in runs_dir.iterdir() if (p / "config.json").exists()),
        key=lambda p: p.name,
        reverse=True,
    )
