from __future__ import annotations

from pathlib import Path

import pytest

from pac.evaluation.application.ports import RunNotFound
from pac.evaluation.domain.run import RunConfig, ScoredRun
from pac.evaluation.infrastructure.run_store import FileRunStore


def _run(run_id: str) -> ScoredRun:
    config = RunConfig(
        run_id=run_id,
        created_at="2026-10-10T00:00:00+00:00",
        classifier="m",
        backend="fake",
        prompt_version="p",
        policy_version="v0",
        policy_sha256="0" * 64,
        dataset_sha256="1" * 64,
        split="dev",
        n_cases=0,
        repeats=1,
        with_history=False,
    )
    return ScoredRun.score(config, [])


def test_save_load_list(tmp_path: Path) -> None:
    store = FileRunStore(tmp_path / "runs")
    assert store.list_ids() == []
    store.save(_run("20261010T000000Z_a"), "# report a")
    store.save(_run("20261011T000000Z_b"), "# report b")

    assert store.list_ids() == ["20261011T000000Z_b", "20261010T000000Z_a"]
    assert store.load("20261010T000000Z_a") == _run("20261010T000000Z_a")
    assert (tmp_path / "runs" / "20261010T000000Z_a" / "report.md").read_text() == "# report a"


@pytest.mark.parametrize("run_id", ["..", ".", "../x", "a/b", "", ".hidden", "missing"])
def test_invalid_or_missing_ids_are_not_found(tmp_path: Path, run_id: str) -> None:
    (tmp_path / "config.json").write_text("{}")  # a decoy one level up
    store = FileRunStore(tmp_path / "runs")
    with pytest.raises(RunNotFound):
        store.load(run_id)
