"""Inter-annotator agreement: how far the gold labels themselves can be trusted."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

from pydantic import BaseModel

from pac.schemas import VERDICTS, Verdict


class Agreement(BaseModel):
    n: int
    observed: float
    expected: float
    kappa: float | None
    confusion: dict[str, dict[str, int]]


def cohen_kappa(a: Sequence[Verdict], b: Sequence[Verdict]) -> Agreement:
    """Cohen's kappa for two annotators over the same items (3-way verdicts).

    ``kappa`` is ``None`` when chance agreement is 1 (both annotators used a single label), where
    the statistic is undefined.
    """
    if len(a) != len(b):
        raise ValueError("annotators must label the same items")
    n = len(a)
    if n == 0:
        raise ValueError("no items")
    observed = sum(x == y for x, y in zip(a, b, strict=True)) / n
    ca, cb = Counter(a), Counter(b)
    expected = sum(ca[v] * cb[v] for v in VERDICTS) / (n * n)
    kappa = None if expected == 1 else (observed - expected) / (1 - expected)
    confusion = {x: dict.fromkeys(VERDICTS, 0) for x in VERDICTS}
    for x, y in zip(a, b, strict=True):
        confusion[x][y] += 1
    return Agreement(
        n=n,
        observed=observed,
        expected=expected,
        kappa=kappa,
        confusion={k: dict(v) for k, v in confusion.items()},
    )
