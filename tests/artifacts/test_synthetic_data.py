"""The committed synthetic data: valid, labelled by the policy's rules, and kept apart.

Guideline 6. Each synthetic split has its own world slice, briefs, templates and seeds, and the
test split its own agent model. These tests prove, on the committed files, that no id, no
template and no near-duplicate event is shared with any data/eval file or across synthetic
splits, and that the adversarial and policy-gap sets never touch the eval world.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

from pac.evaluation.domain.cases import LabeledCase
from pac.evaluation.infrastructure.case_store import load_cases
from pac.guardrail.domain.policy import Policy
from pac.settings import REPO_ROOT
from pac.synthesis.domain.dedup import NearDuplicateIndex
from pac.synthesis.domain.records import SyntheticEvent
from pac.synthesis.domain.specs import EVAL_WORLD_ID
from pac.synthesis.domain.world import build_slice

SYN = REPO_ROOT / "data" / "synthetic"
EVAL = REPO_ROOT / "data" / "eval"
SPLITS = ("train", "dev", "test")


def _events(split: str) -> list[SyntheticEvent]:
    path = SYN / "events" / f"{split}.jsonl"
    return [
        SyntheticEvent.model_validate_json(line) for line in path.read_text("utf-8").splitlines()
    ]


@pytest.fixture(scope="module")
def events() -> dict[str, list[SyntheticEvent]]:
    return {s: _events(s) for s in SPLITS}


@pytest.fixture(scope="module")
def eval_cases() -> list[LabeledCase]:
    cases: list[LabeledCase] = []
    for path in sorted(EVAL.glob("*.jsonl")):
        cases += load_cases(path)
    return cases


def _synthetic_files() -> Iterator[Path]:
    for pattern in (
        "events/*.jsonl",
        "versions/*.jsonl",
        "recordings/**/*.json",
        "*.jsonl",
        "adversarial/*.json*",
        "policy_gaps/*.json*",
        "enforcement/*.json*",
    ):
        yield from SYN.glob(pattern)


def test_every_split_is_present_and_labelled_by_the_policys_rules(
    events: dict[str, list[SyntheticEvent]], policy: Policy
) -> None:
    for split, rows in events.items():
        assert rows, split
        assert {e.split for e in rows} == {split}
        for e in rows:
            assert all(policy.defines(r) for r in (*e.rule_ids, *e.rule_ids_with_history)), e.id
            assert (e.label == "violation") == bool(e.rule_ids), e.id


def test_policy_silent_always_names_a_missing_fact_or_a_conflict(
    events: dict[str, list[SyntheticEvent]],
) -> None:
    for rows in events.values():
        for e in rows:
            if e.label == "policy_silent":
                assert re.search(r"missing|conflict", e.rationale), e.id


def _ids_in(paths: list[Path]) -> set[str]:
    pattern = re.compile(r"\bC-\d{4}\b|\bLK-\d{5}\b")
    return {i for p in paths for i in pattern.findall(p.read_text("utf-8"))}


def test_no_eval_or_train_id_appears_in_any_synthetic_file() -> None:
    """Every customer and order id used by data/eval or data/train is absent from data/synthetic.

    (A range check would be wrong: simulated customers sometimes invent an order number, such
    as an adversarial customer asking about "my neighbour's order #LK-88210", which no eval or
    train file contains.)
    """
    used = _ids_in([*EVAL.glob("*.jsonl"), *(REPO_ROOT / "data" / "train").glob("*")])
    assert len(used) > 50
    for path in _synthetic_files():
        found = sorted(used & _ids_in([path]))
        assert not found, (path, found[:3])


def test_the_synthetic_world_never_uses_an_eval_range_id() -> None:
    for name in ("train", "dev", "test", "adversarial"):
        world = build_slice(name)
        assert not EVAL_WORLD_ID.search(" ".join(sorted(world.identifiers())))


def test_each_split_uses_only_its_own_world_slice(events: dict[str, list[SyntheticEvent]]) -> None:
    ids = {s: build_slice(s).identifiers() for s in SPLITS}  # type: ignore[arg-type]
    for split, rows in events.items():
        text = "\n".join(e.model_dump_json() for e in rows)
        for other in SPLITS:
            if other != split:
                leaked = sorted(i for i in ids[other] if i in text)
                assert leaked == [], (split, other, leaked[:5])


def test_no_template_is_shared_across_splits(events: dict[str, list[SyntheticEvent]]) -> None:
    templates = {s: {e.template for e in rows} for s, rows in events.items()}
    for i, a in enumerate(SPLITS):
        for b in SPLITS[i + 1 :]:
            assert not templates[a] & templates[b], (a, b, sorted(templates[a] & templates[b])[:5])


def test_the_test_split_holds_out_its_agent_model(events: dict[str, list[SyntheticEvent]]) -> None:
    agents = {s: {e.agent_model for e in rows if e.agent_model} for s, rows in events.items()}
    assert agents["test"]
    assert not agents["test"] & (agents["train"] | agents["dev"])


def test_no_synthetic_event_repeats_or_nearly_repeats_an_eval_event(
    events: dict[str, list[SyntheticEvent]], eval_cases: list[LabeledCase]
) -> None:
    index = NearDuplicateIndex()
    for case in eval_cases:
        for event in (case.request.event, *case.request.history):
            index.add(event, case.id)
    for rows in events.values():
        near = {e.id: index.near_duplicate(e.request.event) for e in rows}
        assert {k: v for k, v in near.items() if v is not None} == {}


def test_no_event_nearly_repeats_one_in_another_split(
    events: dict[str, list[SyntheticEvent]],
) -> None:
    for i, a in enumerate(SPLITS):
        index = NearDuplicateIndex(e.request.event for e in events[a])
        for b in SPLITS[i + 1 :]:
            clashes = [e.id for e in events[b] if index.near_duplicate(e.request.event) is not None]
            assert clashes == [], (a, b, clashes[:5])


def test_versions_load_as_labelled_cases_for_prompt_optimisation() -> None:
    for split in SPLITS:
        for version in ("base_rate", "enriched"):
            cases = load_cases(SYN / "versions" / f"{split}_{version}.jsonl")
            assert cases, (split, version)
            assert {c.split for c in cases} == {"train"}


def test_red_team_outputs_never_use_a_synthetic_test_split_id() -> None:
    test_ids = build_slice("test").identifiers()
    for name in ("adversarial/archive.json", "policy_gaps.jsonl", "audit_sample.jsonl"):
        path = SYN / name
        if path.exists():
            text = path.read_text("utf-8")
            assert not [i for i in test_ids if i in text], name


def test_review_queue_rows_point_at_real_events(events: dict[str, list[SyntheticEvent]]) -> None:
    ids = {e.id for rows in events.values() for e in rows}
    path = SYN / "review_queue.jsonl"
    rows = [json.loads(line) for line in path.read_text("utf-8").splitlines()]
    assert rows
    assert all(r["id"] in ids and r["reasons"] for r in rows)
