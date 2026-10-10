"""Curated demo scenarios for the UI: eval cases plus a title and what to look for."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel, TypeAdapter

from pac.evaluation.domain.cases import LabeledCase


class ExampleRef(BaseModel):
    case_id: str
    title: str
    look_for: str


class DemoExample(BaseModel):
    title: str
    look_for: str
    case: LabeledCase


def load_examples(path: Path, cases: Sequence[LabeledCase]) -> list[DemoExample]:
    """Resolve the curated references against the eval set; unknown ids are an error."""
    refs = TypeAdapter(list[ExampleRef]).validate_python(json.loads(path.read_text()))
    by_id = {c.id: c for c in cases}
    missing = [r.case_id for r in refs if r.case_id not in by_id]
    if missing:
        raise ValueError(f"demo examples reference unknown cases: {missing}")
    return [DemoExample(title=r.title, look_for=r.look_for, case=by_id[r.case_id]) for r in refs]
