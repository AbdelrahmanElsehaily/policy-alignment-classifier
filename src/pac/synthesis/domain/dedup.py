"""Exact and near-duplicate events (guidelines 6 and 7).

The same approach as data/builders/build_train_cases.py, which decontaminated the train split:
an event is compared by its text (for a tool call, its argument values only, since every refund
call shares the argument names), as word trigrams with digit runs masked, so a rephrasing with
new amounts or ids still matches. Two events are near-duplicates at a trigram Jaccard of 0.6 or
more. Texts shorter than eight tokens are compared exactly, never by overlap.
tests/unit/synthesis/domain/test_dedup.py checks that this matches the builder's functions.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from pac.guardrail.domain.events import TraceEvent

NEAR_DUPLICATE_JACCARD = 0.6
MIN_TOKENS_FOR_OVERLAP = 8

Grams = frozenset[tuple[str, ...]]


def event_text(event: TraceEvent) -> str:
    """The text two events could share. For a tool call, the argument values only."""
    if event.content is not None:
        return event.content
    return " ".join(str(v) for _, v in sorted((event.arguments or {}).items()))


def normalise(text: str) -> str:
    """Lower case, punctuation to spaces, whitespace collapsed; digits kept."""
    return " ".join(re.sub(r"[^\w]+", " ", text.lower()).split())


def trigrams(text: str) -> Grams:
    """Word trigrams with every digit run masked, so '$24.99' and '$31.50' look the same."""
    tokens = re.sub(r"\d+", "0", normalise(text)).split()
    if len(tokens) < MIN_TOKENS_FOR_OVERLAP:
        return frozenset()
    return frozenset(zip(tokens, tokens[1:], tokens[2:], strict=False))


def jaccard(a: Grams, b: Grams) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


class NearDuplicateIndex:
    """Events indexed for exact (normalised text) and near-duplicate (trigram) lookups."""

    def __init__(self, events: Iterable[TraceEvent] = ()) -> None:
        self._normalised: dict[str, str] = {}
        self._grams: list[tuple[Grams, str]] = []
        for event in events:
            self.add(event)

    def __len__(self) -> int:
        return len(self._normalised)

    def add(self, event: TraceEvent, label: str = "") -> None:
        text = event_text(event)
        self._normalised.setdefault(normalise(text), label)
        grams = trigrams(text)
        if grams:
            self._grams.append((grams, label))

    def closest(self, event: TraceEvent) -> tuple[float, str]:
        """The highest overlap with an indexed event, and that event's label."""
        text = event_text(event)
        key = normalise(text)
        if key in self._normalised:
            return 1.0, self._normalised[key]
        grams = trigrams(text)
        if not grams:
            return 0.0, ""
        best, label = 0.0, ""
        for other, other_label in self._grams:
            score = jaccard(grams, other)
            if score > best:
                best, label = score, other_label
        return best, label

    def near_duplicate(self, event: TraceEvent) -> float | None:
        """The overlap with the closest indexed event if it is a near-duplicate, else None."""
        score, _ = self.closest(event)
        return score if score >= NEAR_DUPLICATE_JACCARD else None


def novelty(event: TraceEvent, others: Iterable[TraceEvent]) -> float:
    """One minus the highest trigram overlap with ``others`` (1.0 when nothing is similar)."""
    index = NearDuplicateIndex(others)
    return 1.0 - index.closest(event)[0]
