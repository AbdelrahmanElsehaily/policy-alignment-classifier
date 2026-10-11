"""The curated UI examples must point at real eval cases."""

from __future__ import annotations

from pac.evaluation.infrastructure.case_store import load_cases
from pac.interfaces.demo_examples import load_examples
from pac.settings import REPO_ROOT


def test_examples_resolve_and_cover_every_verdict() -> None:
    cases = load_cases(REPO_ROOT / "data" / "eval" / "dev_test.jsonl")
    examples = load_examples(REPO_ROOT / "data" / "ui" / "demo_examples.json", cases)
    assert len(examples) >= 10
    assert {e.case.label for e in examples} == {"allow", "violation", "policy_silent"}
    assert {e.case.request.event.kind for e in examples} == {
        "user_input",
        "model_output",
        "tool_call",
        "tool_response",
    }
    assert any(e.case.label_with_history is not None for e in examples)
