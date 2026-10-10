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
