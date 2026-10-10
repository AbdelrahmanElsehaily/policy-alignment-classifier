from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from pac.guardrail.application.prompting import PromptTemplate
from pac.guardrail.domain.events import ClassificationRequest, Session, TraceEvent
from pac.guardrail.domain.policy import Policy
from pac.guardrail.infrastructure.files import load_policy, load_prompt
from pac.settings import Settings, get_settings


@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Keep the developer's shell environment and .env out of the test suite."""
    for var in (
        "PAC_BACKEND",
        "PAC_MODEL",
        "ANTHROPIC_API_KEY",
        "MINIMAX_API_KEY",
        "PAC_CACHE_DIR",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(Path(__file__).parent)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(scope="session")
def policy() -> Policy:
    return load_policy(Settings().policy_path)


@pytest.fixture(scope="session")
def prompt(policy: Policy) -> PromptTemplate:
    return load_prompt(Settings().prompt_path, policy)


@pytest.fixture
def verified_session() -> Session:
    return Session(verified=True, verified_customer_id="C-1001", email_on_file="ana@mail.example")


@pytest.fixture
def refund_request(verified_session: Session) -> ClassificationRequest:
    return ClassificationRequest(
        session=verified_session,
        event=TraceEvent(
            kind="tool_call",
            tool_name="issue_refund",
            arguments={"order_id": "LK-1", "amount": 450.0, "reason": "changed mind"},
        ),
    )


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
