"""Integration: the CLI commands against a temporary dataset, offline."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pac.cli import app
from pac.schemas import ClassificationRequest, LabeledCase, TraceEvent

pytestmark = pytest.mark.integration
runner = CliRunner()
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def plain(text: str) -> str:
    """CLI output without colour codes, panel borders or wrapping (CI terminals add all three)."""
    return " ".join(ANSI.sub("", text).replace("│", " ").split())


@pytest.fixture
def dataset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cases = [
        LabeledCase(
            id="card-in-output",
            split="dev",
            request=ClassificationRequest(
                event=TraceEvent(kind="model_output", content="Your card 4111 1111 1111 1111")
            ),
            label="violation",
            rule_ids=("R3",),
            label_confidence="high",
            rationale="Full card number in Lark's output.",
        ),
        LabeledCase(
            id="greeting",
            split="test",
            request=ClassificationRequest(event=TraceEvent(kind="user_input", content="hi")),
            label="allow",
            label_confidence="high",
            rationale="Benign.",
        ),
    ]
    path = tmp_path / "cases.jsonl"
    path.write_text("".join(c.model_dump_json() + "\n" for c in cases))
    monkeypatch.setenv("PAC_CASES_PATH", str(path))
    monkeypatch.setenv("PAC_RESULTS_DIR", str(tmp_path / "results"))
    monkeypatch.setenv("PAC_CACHE_DIR", str(tmp_path / "cache"))
    return path


def test_check_data(dataset: Path) -> None:
    result = runner.invoke(app, ["check-data"])
    assert result.exit_code == 0, result.output
    assert "2 cases (1 dev, 1 test)" in result.output


def test_check_data_reports_bad_rule(dataset: Path) -> None:
    line = json.loads(dataset.read_text().splitlines()[0])
    line["rule_ids"] = ["R42"]
    dataset.write_text(json.dumps(line) + "\n")
    result = runner.invoke(app, ["check-data"])
    assert result.exit_code == 1
    assert "R42" in result.output


def test_eval_baseline_writes_a_run(dataset: Path, tmp_path: Path) -> None:
    out = tmp_path / "run"
    result = runner.invoke(
        app, ["eval", "--split", "all", "--classifier", "baseline", "--out-dir", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert {p.name for p in out.iterdir()} == {
        "config.json",
        "predictions.jsonl",
        "metrics.json",
        "report.md",
    }
    metrics = json.loads((out / "metrics.json").read_text())
    assert metrics["accuracy"]["k"] == 2

    rerendered = runner.invoke(app, ["report", str(out)])
    assert rerendered.exit_code == 0


def test_eval_replay_with_empty_cache_fails_closed(dataset: Path, tmp_path: Path) -> None:
    out = tmp_path / "run"
    result = runner.invoke(
        app,
        ["eval", "--split", "dev", "--backend", "replay", "--model", "m", "--out-dir", str(out)],
    )
    assert result.exit_code == 0, result.output
    metrics = json.loads((out / "metrics.json").read_text())
    assert metrics["operational"]["classifier_errors"]["k"] == 1
    assert metrics["confusion"]["violation"]["policy_silent"] == 1


def test_eval_rejects_repeats_with_cache(dataset: Path) -> None:
    result = runner.invoke(app, ["eval", "--classifier", "baseline", "--repeats", "3"])
    assert result.exit_code != 0
    assert "Add --no-cache" in plain(result.output)


def test_eval_unknown_case(dataset: Path) -> None:
    result = runner.invoke(app, ["eval", "--classifier", "baseline", "--case", "nope"])
    assert result.exit_code == 1


def test_classify_missing_key_is_a_clean_error(tmp_path: Path) -> None:
    request = tmp_path / "req.json"
    request.write_text(
        ClassificationRequest(event=TraceEvent(kind="user_input", content="hi")).model_dump_json()
    )
    result = runner.invoke(app, ["classify", str(request), "--backend", "anthropic"])
    assert result.exit_code == 1
    assert "ANTHROPIC_API_KEY" in result.output


def test_classify_from_stdin_with_replay(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAC_CACHE_DIR", str(tmp_path))
    payload = ClassificationRequest(
        event=TraceEvent(kind="user_input", content="hi")
    ).model_dump_json()
    result = runner.invoke(app, ["classify", "--backend", "replay", "--model", "m"], input=payload)
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["decision"]["verdict"] == "policy_silent"


def test_unknown_backend(tmp_path: Path) -> None:
    result = runner.invoke(app, ["classify", "--backend", "gpt"], input="{}")
    assert result.exit_code != 0
