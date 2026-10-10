"""Runs on disk: ``<root>/<run_id>/{config.json, predictions.jsonl, metrics.json, report.md}``."""

from __future__ import annotations

import json
import re
from pathlib import Path

from pac.evaluation.application.ports import RunNotFound
from pac.evaluation.domain.metrics import Metrics
from pac.evaluation.domain.prediction import Prediction
from pac.evaluation.domain.run import RunConfig, ScoredRun

# A run id is a single path segment: no separators, and never '.' or '..'.
RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class FileRunStore:
    """Implements :class:`~pac.evaluation.application.ports.RunStore` on the filesystem."""

    def __init__(self, root: Path) -> None:
        self._root = root

    @property
    def root(self) -> Path:
        return self._root

    def _dir(self, run_id: str) -> Path:
        if not RUN_ID.match(run_id):
            raise RunNotFound(f"invalid run id {run_id!r}")
        return self._root / run_id

    def save(self, run: ScoredRun, report: str) -> None:
        run_dir = self._dir(run.config.run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "config.json").write_text(run.config.model_dump_json(indent=2) + "\n")
        (run_dir / "predictions.jsonl").write_text(
            "".join(p.model_dump_json() + "\n" for p in run.predictions)
        )
        (run_dir / "metrics.json").write_text(run.metrics.model_dump_json(indent=2) + "\n")
        (run_dir / "report.md").write_text(report)

    def load(self, run_id: str) -> ScoredRun:
        run_dir = self._dir(run_id)
        if not (run_dir / "config.json").exists():
            raise RunNotFound(f"no run {run_id!r}")
        config = RunConfig.model_validate_json((run_dir / "config.json").read_text())
        predictions = tuple(
            Prediction.model_validate_json(line)
            for line in (run_dir / "predictions.jsonl").read_text().splitlines()
            if line.strip()
        )
        metrics = Metrics.model_validate(json.loads((run_dir / "metrics.json").read_text()))
        return ScoredRun(config=config, predictions=predictions, metrics=metrics)

    def list_ids(self) -> list[str]:
        if not self._root.is_dir():
            return []
        return sorted(
            (p.name for p in self._root.iterdir() if (p / "config.json").exists()),
            reverse=True,
        )
