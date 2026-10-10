"""Integration: a live conversation turn streamed over HTTP, with fake models on both sides."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from pac.guardrail.application.llm_classifier import LLMClassifier
from pac.guardrail.application.ports import LLMRequest
from pac.guardrail.application.prompting import PromptTemplate
from pac.guardrail.domain.policy import Policy
from pac.interfaces.api import create_app
from pac.settings import Settings
from pac.simulation.application.agent import SimulatedAgent
from tests.support.builders import decision_json
from tests.support.fakes import FakeBackend

pytestmark = pytest.mark.integration

ANA = {"verified": True, "verified_customer_id": "C-1001", "email_on_file": "ana.ruiz@mail.example"}


def _lark(request: LLMRequest) -> str:
    """Look the order up once, then reply."""
    if "lookup_order RETURNED" in request.user:
        return json.dumps({"action": "reply", "content": "Your order was delivered."})
    return json.dumps(
        {"action": "tool_call", "tool_name": "lookup_order", "arguments": {"order_id": "LK-50402"}}
    )


def _guard(request: LLMRequest) -> str:
    # The order lookup returns a live key in its notes: an R4 leak in the tool response.
    if "sk_live_" in request.user.split("<event>")[-1]:
        return decision_json("violation", ["R4"])
    return decision_json("allow")


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(results_dir=tmp_path / "results", web_dist=tmp_path / "no-dist")


@pytest.fixture
def client(settings: Settings, policy: Policy, prompt: PromptTemplate) -> TestClient:
    classifier = LLMClassifier(FakeBackend(_guard), policy, prompt)
    agent = SimulatedAgent(FakeBackend(_lark, model="fake-lark"), "You are Lark.")
    return TestClient(create_app(settings, classifier, agent))


def _turn(client: TestClient, **body: Any) -> list[dict[str, Any]]:
    with client.stream("POST", "/api/conversation/turn", json=body) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/x-ndjson")
        return [json.loads(line) for line in response.iter_lines() if line]


def test_setup_lists_customers_and_suggestions(client: TestClient) -> None:
    setup = client.get("/api/conversation").json()
    assert setup["available"] is True
    assert setup["agent_model"] == "fake-lark"
    assert [c["customer_id"] for c in setup["customers"]] == ["C-1001", "C-1002"]
    customers = {c["customer_id"] for c in setup["customers"]}
    assert len(setup["suggestions"]) >= 8
    assert all(s["verify_as"] in customers | {None} for s in setup["suggestions"])


def test_a_turn_streams_events_then_verdicts(client: TestClient) -> None:
    updates = _turn(client, session=ANA, history=[], message="Where is LK-50402?")

    events = [u for u in updates if u["type"] == "event"]
    assert [e["event"]["kind"] for e in events] == [
        "user_input",
        "tool_call",
        "tool_response",
        "model_output",
    ]
    llm = {
        u["index"]: u["classification"]
        for u in updates
        if u["type"] == "verdict" and u["engine"] == "llm"
    }
    baseline = [u for u in updates if u["type"] == "verdict" and u["engine"] == "baseline"]
    assert llm[2]["decision"]["verdict"] == "violation"
    assert llm[2]["decision"]["rule_ids"] == ["R4"]
    assert len(baseline) == 4
    assert updates[-1] == {"type": "done"}


def test_history_replays_side_effects(client: TestClient) -> None:
    refund = {
        "kind": "tool_call",
        "tool_name": "issue_refund",
        "arguments": {"order_id": "LK-50402", "amount": 20, "reason": "late"},
    }
    updates = _turn(client, session=ANA, history=[refund], message="And the order?")
    response = next(
        u for u in updates if u["type"] == "event" and u["event"]["kind"] == "tool_response"
    )
    assert json.loads(response["event"]["content"])["refunded"] == 20
    assert updates[0]["index"] == 1


def test_turn_validates_the_message(client: TestClient) -> None:
    assert client.post("/api/conversation/turn", json={"message": ""}).status_code == 422


def test_unavailable_without_live_mode(settings: Settings) -> None:
    client = TestClient(create_app(settings))  # replay backend, not live
    setup = client.get("/api/conversation").json()
    assert setup["available"] is False
    assert "pac serve --live" in setup["error"]
    response = client.post("/api/conversation/turn", json={"message": "hi"})
    assert response.status_code == 503
