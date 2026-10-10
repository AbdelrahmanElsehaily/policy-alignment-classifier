"""The committed eval set: valid, in sync with its builder, and internally consistent."""

from __future__ import annotations

import subprocess
import sys
from collections import Counter

from pac.eval.dataset import check_against_policy, load_cases
from pac.policy import Policy
from pac.schemas import EVENT_KINDS
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
