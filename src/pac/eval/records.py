"""What an eval run writes to disk."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from pac.schemas import (
    Classification,
    EventKind,
    LabelConfidence,
    LabeledCase,
    Split,
    Verdict,
)


class Prediction(BaseModel):
    """One classifier output joined with the gold label it is scored against."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    repeat: int = 0
    split: Split
    kind: EventKind
    gold: Verdict
    gold_rules: tuple[str, ...]
    label_confidence: LabelConfidence
    tags: tuple[str, ...]
    origin: str
    result: Classification

    @property
    def predicted(self) -> Verdict:
        return self.result.decision.verdict

    @property
    def correct(self) -> bool:
        return self.predicted == self.gold

    @classmethod
    def from_case(cls, case: LabeledCase, result: Classification, repeat: int = 0) -> Prediction:
        return cls(
            case_id=case.id,
            repeat=repeat,
            split=case.split,
            kind=case.request.event.kind,
            gold=case.label,
            gold_rules=case.rule_ids,
            label_confidence=case.label_confidence,
            tags=case.tags,
            origin=case.origin,
            result=result,
        )


class RunConfig(BaseModel):
    """Everything needed to tell what a run measured and to reproduce it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    created_at: str
    classifier: str
    backend: str
    prompt_version: str
    policy_version: str
    policy_sha256: str
    dataset_sha256: str
    split: str
    n_cases: int
    repeats: int
    with_history: bool
    git_commit: str | None = None
