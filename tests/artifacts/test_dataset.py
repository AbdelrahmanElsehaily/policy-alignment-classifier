"""The committed eval set: valid, in sync with its builder, and internally consistent."""

from __future__ import annotations

import subprocess
import sys
from collections import Counter

from pac.evaluation.domain.cases import LabeledCase, check_against_policy
from pac.evaluation.infrastructure.case_store import load_cases
from pac.guardrail.domain.events import EVENT_KINDS
from pac.guardrail.domain.policy import Policy
from pac.settings import REPO_ROOT, Settings


def test_dataset_valid_against_policy(policy: Policy) -> None:
    cases = load_cases(Settings().cases_path)
    check_against_policy(cases, policy)
    assert len(cases) >= 150


def test_jsonl_matches_builder() -> None:
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "data" / "build_cases.py"), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_every_kind_and_verdict_in_both_splits() -> None:
    cases = load_cases(Settings().cases_path)
    for split in ("dev", "test"):
        subset = [c for c in cases if c.split == split]
        assert {c.request.event.kind for c in subset} == set(EVENT_KINDS)
        assert {c.label for c in subset} == {"allow", "violation", "policy_silent"}


def test_transcripts_stay_in_one_split() -> None:
    cases = load_cases(Settings().cases_path)
    splits: dict[str, set[str]] = {}
    for c in cases:
        if c.origin == "transcript":
            splits.setdefault(c.id.rsplit("-", 1)[0], set()).add(c.split)
    assert splits
    assert all(len(s) == 1 for s in splits.values())


def test_corner_cases_are_not_marked_high_confidence() -> None:
    cases = load_cases(Settings().cases_path)
    offenders = [c.id for c in cases if "corner-case" in c.tags and c.label_confidence == "high"]
    assert not offenders


def test_position_pairs_share_content_across_kinds() -> None:
    cases = load_cases(Settings().cases_path)
    pairs = Counter(c.id.split("-")[0] for c in cases if "position-pair" in c.tags)
    assert all(n >= 3 for n in pairs.values())


def test_no_test_event_duplicates_a_dev_event() -> None:
    """The classifier sees one event; an identical dev event would leak prompt tuning into test."""
    cases = load_cases(Settings().cases_path)

    def key(c: LabeledCase) -> str:
        return c.request.model_copy(update={"history": ()}).model_dump_json()

    dev = {key(c) for c in cases if c.split == "dev"}
    leaked = [c.id for c in cases if c.split == "test" and key(c) in dev]
    assert not leaked


def test_contested_labels_are_tagged_corner_case() -> None:
    cases = load_cases(Settings().cases_path)
    untagged = [
        c.id for c in cases if c.label_confidence == "contested" and "corner-case" not in c.tags
    ]
    assert not untagged


# --- the hard split (data/hard_cases.jsonl) ---------------------------------------------------


def _hard() -> list[LabeledCase]:
    return load_cases(Settings().hard_cases_path)


def test_hard_split_valid_and_in_its_own_file(policy: Policy) -> None:
    hard = _hard()
    check_against_policy(hard, policy)
    assert len(hard) >= 30
    assert {c.split for c in hard} == {"hard"}
    assert all("hard" in c.tags for c in hard)
    assert not any(c.split == "hard" for c in load_cases(Settings().cases_path))


def test_hard_jsonl_matches_builder() -> None:
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "data" / "build_hard_cases.py"), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_hard_split_covers_every_kind_and_verdict() -> None:
    hard = _hard()
    assert {c.request.event.kind for c in hard} == set(EVENT_KINDS)
    assert {c.label for c in hard} == {"allow", "violation", "policy_silent"}


def test_hard_split_mostly_needs_more_than_history() -> None:
    """The point of the split: history settles a few cases, but many stay undecided."""
    hard = _hard()
    still_silent = [c for c in hard if c.gold(with_history=True)[0] == "policy_silent"]
    resolved = [c for c in hard if c.label_with_history not in (None, "policy_silent")]
    assert len(still_silent) >= 5
    assert len(resolved) >= 5


def test_no_hard_event_duplicates_a_dev_event() -> None:
    def key(c: LabeledCase) -> str:
        return c.request.event.model_dump_json() + c.request.session.model_dump_json()

    dev = {key(c) for c in load_cases(Settings().cases_path) if c.split == "dev"}
    assert [c.id for c in _hard() if key(c) in dev] == []
