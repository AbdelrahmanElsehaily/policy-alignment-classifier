"""The eval set on disk: one JSON ``LabeledCase`` per line."""

from __future__ import annotations

import hashlib
from pathlib import Path

from pac.evaluation.domain.cases import DatasetError, LabeledCase, ensure_unique_ids


def load_cases(path: Path) -> list[LabeledCase]:
    cases: list[LabeledCase] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            cases.append(LabeledCase.model_validate_json(line))
        except ValueError as exc:
            raise DatasetError(f"{path}:{line_no}: {exc}") from exc
    ensure_unique_ids(cases)
    return cases


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
