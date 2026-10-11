"""An eval run: what was measured, the raw predictions, and their scores."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from pac.evaluation.domain.metrics import Metrics, compute_metrics
from pac.evaluation.domain.prediction import Prediction
from pac.guardrail.domain.value_object import ValueObject


class RunConfig(ValueObject):
    """Everything needed to tell what a run measured and to reproduce it."""

    run_id: str
    created_at: str
    classifier: str
    backend: str
    prompt_version: str
    policy_version: str
    policy_sha256: str
    dataset_sha256: str
    # The dataset file under data/, so a run can be replayed against the file it measured.
    dataset: str = "cases.jsonl"
    split: str
    n_cases: int
    repeats: int
    with_history: bool
    git_commit: str | None = None


@dataclass(frozen=True, slots=True)
class ScoredRun:
    """A run with its metrics. Metrics are always derived from the predictions, never edited."""

    config: RunConfig
    predictions: tuple[Prediction, ...]
    metrics: Metrics

    @classmethod
    def score(cls, config: RunConfig, predictions: Sequence[Prediction]) -> ScoredRun:
        ordered = tuple(sorted(predictions, key=lambda p: (p.case_id, p.repeat)))
        return cls(config=config, predictions=ordered, metrics=compute_metrics(ordered))
