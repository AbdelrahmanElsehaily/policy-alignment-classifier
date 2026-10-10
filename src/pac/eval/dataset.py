"""Loading and checking the labelled eval set."""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

from pac.policy import Policy
from pac.schemas import LabeledCase, Split


class DatasetError(ValueError):
    pass


def load_cases(path: Path) -> list[LabeledCase]:
    cases: list[LabeledCase] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            cases.append(LabeledCase.model_validate_json(line))
        except ValueError as exc:
            raise DatasetError(f"{path}:{line_no}: {exc}") from exc
    duplicates = [i for i, n in Counter(c.id for c in cases).items() if n > 1]
    if duplicates:
        raise DatasetError(f"duplicate case ids: {sorted(duplicates)}")
    return cases


def check_against_policy(cases: Iterable[LabeledCase], policy: Policy) -> None:
    """Every rule a label cites must exist in the policy it is measured against."""
    for case in cases:
        unknown = set(case.rule_ids) - policy.rule_ids
        if unknown:
            raise DatasetError(f"{case.id}: cites rules not in policy: {sorted(unknown)}")


def select(cases: Iterable[LabeledCase], split: Split | None) -> list[LabeledCase]:
    return [c for c in cases if split is None or c.split == split]


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
