"""Agreement and diversity statistics for the synthetic data (guidelines 5 and 7)."""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Hashable, Iterable, Mapping, Sequence


def agreement(a: Sequence[Hashable], b: Sequence[Hashable]) -> float:
    if len(a) != len(b):
        raise ValueError("label sequences differ in length")
    return sum(1 for x, y in zip(a, b, strict=True) if x == y) / len(a) if a else 0.0


def cohen_kappa(a: Sequence[Hashable], b: Sequence[Hashable]) -> float:
    """Cohen's kappa: agreement beyond what the two label distributions give by chance."""
    if len(a) != len(b):
        raise ValueError("label sequences differ in length")
    n = len(a)
    if n == 0:
        return 0.0
    observed = agreement(a, b)
    ca, cb = Counter(a), Counter(b)
    expected = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    if expected >= 1.0:
        return 1.0 if observed == 1.0 else 0.0
    return (observed - expected) / (1 - expected)


def fleiss_kappa(ratings: Sequence[Sequence[Hashable]]) -> float:
    """Fleiss' kappa for items each rated by the same number (>= 2) of raters."""
    items = [list(r) for r in ratings if len(r) >= 2]
    if not items:
        return 0.0
    raters = len(items[0])
    items = [r for r in items if len(r) == raters]
    categories = sorted({c for r in items for c in r}, key=str)
    n = len(items)
    p_items = []
    totals: Counter[Hashable] = Counter()
    for r in items:
        counts = Counter(r)
        totals.update(counts)
        p_items.append((sum(c * c for c in counts.values()) - raters) / (raters * (raters - 1)))
    p_bar = sum(p_items) / n
    p_e = sum((totals[c] / (n * raters)) ** 2 for c in categories)
    if p_e >= 1.0:
        return 1.0 if p_bar == 1.0 else 0.0
    return (p_bar - p_e) / (1 - p_e)


def confusion(
    gold: Sequence[str], predicted: Sequence[str], labels: Sequence[str]
) -> dict[str, dict[str, int]]:
    table = {g: dict.fromkeys(labels, 0) for g in labels}
    for g, p in zip(gold, predicted, strict=True):
        if g in table and p in table[g]:
            table[g][p] += 1
    return table


def wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0.0, 0.0
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


_TOKEN = re.compile(r"\w+", re.UNICODE)


def tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def distinct_n(texts: Iterable[str], n: int) -> float:
    """Distinct n-grams over all n-grams (Li et al., 2016): 1.0 means no n-gram repeats."""
    total = 0
    seen: set[tuple[str, ...]] = set()
    for text in texts:
        toks = tokens(text)
        grams = list(zip(*(toks[i:] for i in range(n)), strict=False))
        total += len(grams)
        seen.update(grams)
    return len(seen) / total if total else 0.0


def length_buckets(texts: Iterable[str]) -> dict[str, int]:
    """Character lengths in the drift monitor's buckets."""
    edges = ((50, "<50"), (150, "50-150"), (400, "150-400"), (1000, "400-1000"))
    counts: Counter[str] = Counter()
    for text in texts:
        label = next((name for edge, name in edges if len(text) < edge), "1000+")
        counts[label] += 1
    return {name: counts.get(name, 0) for name in ("<50", "50-150", "150-400", "400-1000", "1000+")}


def per_group(values: Mapping[str, Iterable[Hashable]]) -> dict[str, int]:
    return {k: len(list(v)) for k, v in values.items()}
