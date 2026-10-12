"""The coverage grid (guideline 3): which kinds of event the data has, and which it lacks.

The full grid (event kind x rule x verdict x position x verification x language x attack style
x tool outcome) has hundreds of thousands of cells, almost all of them meaningless (an R13
violation in a tool result). Coverage is tracked on projections instead, the way combinatorial
testing covers pairs rather than the full product: each projection names two or three axes and
which of their cells are valid. The primary projection is kind x rule x verdict; a violation
cell is valid only where the rule can judge the kind, and allow and silent cells also where the
rule's look-alikes live (a customer pasting their own card is the R3 look-alike in a
user_input).

The grid also produces the two versions of a split: realistic base rates (the natural
conversations as they happened, mostly benign) and enriched (capped per cell, every rare cell
kept, and rare-but-important cells oversampled).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass

from pac.guardrail.domain.decision import VERDICTS
from pac.guardrail.domain.events import EVENT_KINDS
from pac.synthesis.domain.records import ATTACK_STYLES, Cell, SyntheticEvent
from pac.synthesis.domain.specs import BoundarySpec

# Rules whose violations move money or leak data: the cells worth oversampling.
IMPORTANT_RULES = frozenset({"R1", "R2", "R3", "R4", "R5", "R7", "R12"})
LANGUAGES = ("en", "es", "fr", "de", "pt", "ar")
POSITIONS = ("single", "opening", "early", "mid", "late")
OUTCOMES = {
    "tool_call": ("first_try", "after_fault"),
    "tool_response": ("ok", "error", "timeout", "partial"),
}

Key = tuple[str, ...]


@dataclass(frozen=True)
class Projection:
    name: str
    axes: tuple[str, ...]
    valid: tuple[Key, ...]

    def key(self, cell: Cell) -> Key:
        return tuple(str(getattr(cell, axis)) for axis in self.axes)


def projections(specs: Sequence[BoundarySpec]) -> tuple[Projection, ...]:
    """The projections coverage is reported on, each with its valid cells."""
    # Where a rule judges a kind, all three verdicts; where only its look-alikes live, allow
    # (a rule can be neither broken nor silent in a kind it doesn't judge). "none" is ordinary
    # traffic no rule is about; every tool call has a topic rule, so it has no "none" cell.
    primary = [
        (kind, spec.rule_id, verdict)
        for spec in specs
        for kind in spec.kinds()
        for verdict in VERDICTS
        if kind in spec.applies_to or verdict == "allow"
    ]
    primary += [(kind, "none", "allow") for kind in EVENT_KINDS if kind != "tool_call"]
    return (
        Projection("kind x rule x verdict", ("kind", "rule", "verdict"), tuple(primary)),
        Projection(
            "kind x verification x verdict",
            ("kind", "verification", "verdict"),
            tuple(
                (k, v, d) for k in EVENT_KINDS for v in ("verified", "unverified") for d in VERDICTS
            ),
        ),
        Projection(
            "kind x position",
            ("kind", "position"),
            tuple((k, p) for k in EVENT_KINDS for p in POSITIONS),
        ),
        Projection(
            "language x verdict",
            ("language", "verdict"),
            tuple((lang, d) for lang in LANGUAGES for d in VERDICTS),
        ),
        Projection(
            "attack style x verdict",
            ("attack_style", "verdict"),
            tuple((s, d) for s in ATTACK_STYLES for d in VERDICTS),
        ),
        Projection(
            "kind x tool outcome",
            ("kind", "tool_outcome"),
            tuple((k, o) for k, outcomes in OUTCOMES.items() for o in outcomes),
        ),
    )


@dataclass(frozen=True)
class Coverage:
    projection: Projection
    counts: Mapping[Key, int]

    @property
    def filled(self) -> int:
        return sum(1 for cell in self.projection.valid if self.counts.get(cell, 0) > 0)

    @property
    def share(self) -> float:
        return self.filled / len(self.projection.valid) if self.projection.valid else 1.0

    def empty(self) -> list[Key]:
        return [cell for cell in self.projection.valid if self.counts.get(cell, 0) == 0]

    def below(self, target: int) -> list[Key]:
        return [cell for cell in self.projection.valid if self.counts.get(cell, 0) < target]


def coverage(cells: Iterable[Cell], projection: Projection) -> Coverage:
    return Coverage(projection, Counter(projection.key(c) for c in cells))


def importance(key: Key) -> float:
    """How much an empty primary cell is worth filling: violations of money and data rules first."""
    kind, rule, verdict = key
    weight = 2.0 if rule in IMPORTANT_RULES else 1.0
    if verdict == "violation":
        weight *= 1.5
    elif verdict == "allow" and rule != "none":
        weight *= 1.25  # hard negatives: look-alikes the classifier is likely to block
    if kind in ("tool_call", "tool_response"):
        weight *= 1.1
    return weight


def fill_plan(cover: Coverage, *, target: int, budget: int) -> list[Key]:
    """Primary cells below ``target``, most important first, at most ``budget`` of them.

    A cell with ``n`` events asks for ``target - n`` more; requests are interleaved so the
    budget spreads across cells before any one gets a second event.
    """
    wants = sorted(cover.below(target), key=lambda k: (-importance(k), cover.counts.get(k, 0), k))
    plan: list[Key] = []
    round_ = 0
    while len(plan) < budget and wants:
        added = False
        for key in wants:
            if cover.counts.get(key, 0) + round_ < target and len(plan) < budget:
                plan.append(key)
                added = True
        if not added:
            break
        round_ += 1
    return plan


def base_rate(events: Iterable[SyntheticEvent]) -> list[SyntheticEvent]:
    """Realistic base rates: the natural conversations only, as they happened."""
    return [e for e in events if e.source == "conversation"]


def enriched(
    events: Sequence[SyntheticEvent],
    *,
    cap: int,
    rare: int = 3,
    is_important: Callable[[Cell], bool] | None = None,
) -> list[tuple[SyntheticEvent, int]]:
    """A cell-balanced version: at most ``cap`` events per primary cell, rare cells oversampled.

    Returns each kept event with its number of copies: 2 for events in cells with at most
    ``rare`` events that are important (by default a violation of a money or data rule, or a
    look-alike next to one), else 1. Within a cell the earliest events are kept, so the choice
    is deterministic.
    """
    important = is_important or (
        lambda c: c.rule in IMPORTANT_RULES and c.verdict in ("violation", "allow")
    )
    by_cell: dict[Key, list[SyntheticEvent]] = {}
    for event in events:
        key = (event.cell.kind, event.cell.rule, event.cell.verdict)
        by_cell.setdefault(key, []).append(event)
    kept: list[tuple[SyntheticEvent, int]] = []
    for members in by_cell.values():
        # Targeted events and pairs first: they were made for this cell; then conversations.
        ordered = sorted(members, key=lambda e: (e.source == "conversation", e.id))[:cap]
        copies = 2 if len(members) <= rare and important(members[0].cell) else 1
        kept += [(e, copies) for e in ordered]
    return sorted(kept, key=lambda pair: pair[0].id)


def label_mix(labels: Iterable[str]) -> dict[str, float]:
    counts = Counter(labels)
    total = sum(counts.values()) or 1
    return {v: round(counts.get(v, 0) / total, 3) for v in VERDICTS}
