"""Small builders for test data."""

from __future__ import annotations

import json


def decision_json(
    verdict: str = "allow",
    rule_ids: list[str] | None = None,
    confidence: float = 0.9,
    rationale: str = "Because.",
) -> str:
    return json.dumps(
        {
            "rationale": rationale,
            "verdict": verdict,
            "rule_ids": rule_ids or [],
            "confidence": confidence,
        }
    )
