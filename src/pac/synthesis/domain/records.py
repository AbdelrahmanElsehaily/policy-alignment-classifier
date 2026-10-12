"""The synthetic dataset's record: one labelled event, where it came from, and its grid cell."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from pac.guardrail.domain.decision import Verdict
from pac.guardrail.domain.events import ClassificationRequest, EventKind
from pac.guardrail.domain.value_object import ValueObject
from pac.synthesis.domain.labelling import LabelConfidence

SynthSplit = Literal["train", "dev", "test", "adversarial"]
Source = Literal["conversation", "pair", "hard_negative", "targeted", "adversarial", "policy_gap"]
AttackStyle = Literal[
    "none",
    "role_play",
    "injection_in_tool_output",
    "obfuscation",
    "translation",
    "authority_claim",
    "multi_turn_setup",
    "benign_looking",
]
ATTACK_STYLES: tuple[AttackStyle, ...] = (
    "role_play",
    "injection_in_tool_output",
    "obfuscation",
    "translation",
    "authority_claim",
    "multi_turn_setup",
    "benign_looking",
)
Position = Literal["single", "opening", "early", "mid", "late"]
ToolOutcome = Literal["n/a", "ok", "error", "timeout", "partial", "first_try", "after_fault"]


def position_of(turn_index: int | None) -> Position:
    """Where in a conversation an event sits (0-based index), or ``single`` for a lone event."""
    if turn_index is None:
        return "single"
    if turn_index == 0:
        return "opening"
    if turn_index <= 4:
        return "early"
    if turn_index <= 12:
        return "mid"
    return "late"


class Cell(ValueObject):
    """The event's coordinates on the coverage grid (guideline 3)."""

    kind: EventKind
    rule: str  # the rule the label rests on, or the rule a look-alike sits next to; "none"
    verdict: Verdict
    position: Position
    verification: Literal["verified", "unverified"]
    language: str
    attack_style: AttackStyle
    tool_outcome: ToolOutcome


class SyntheticEvent(ValueObject):
    """One labelled synthetic event."""

    id: str = Field(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")
    split: SynthSplit
    request: ClassificationRequest
    label: Verdict
    rule_ids: tuple[str, ...] = ()
    label_confidence: LabelConfidence
    rationale: str
    label_with_history: Verdict | None = None
    rule_ids_with_history: tuple[str, ...] = ()
    source: Source
    template: str  # the brief, pair family or seed it came from: the unit of leakage checks
    cell: Cell
    # Juror -> event verdict, and rule -> labelling function verdict, for audits.
    votes: dict[str, Verdict] = Field(default_factory=dict)
    lf: dict[str, str] = Field(default_factory=dict)
    agent_model: str | None = None
    tags: tuple[str, ...] = ()
