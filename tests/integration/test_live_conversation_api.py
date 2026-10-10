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


def test_a_turn_streams_events_verdicts_and_gates(client: TestClient) -> None:
    updates = _turn(client, session=ANA, history=[], message="Where is LK-50402?")

    events = [u for u in updates if u["type"] == "event"]
    assert [(e["event"]["kind"], e["by_guardrail"]) for e in events] == [
        ("user_input", False),
        ("tool_call", False),
        ("tool_response", False),  # carries the leaked key...
        ("tool_response", True),  # ...so Lark gets the guardrail's stand-in instead
        ("model_output", False),
    ]
    gates = {u["index"]: u["gate"] for u in updates if u["type"] == "gate"}
    assert gates == {0: "pass", 1: "pass", 2: "block", 4: "pass"}
    llm = {
        u["index"]: u["classification"]
        for u in updates
        if u["type"] == "verdict" and u["engine"] == "llm"
    }
    assert llm[2]["decision"]["rule_ids"] == ["R4"]
    assert "R4" in events[3]["event"]["content"]
    baseline = [u for u in updates if u["type"] == "verdict" and u["engine"] == "baseline"]
    assert len(baseline) == 4  # every original event, never the stand-in
    assert updates[-1] == {"type": "done"}


def test_history_replays_only_the_tool_calls_that_ran(client: TestClient) -> None:
    def refund(amount: int, gate: str) -> dict[str, Any]:
        call = {
            "kind": "tool_call",
            "tool_name": "issue_refund",
            "arguments": {"order_id": "LK-50402", "amount": amount, "reason": "late"},
        }
        return {"event": call, "gate": gate}

    history = [refund(20, "pass"), refund(30, "block")]
    updates = _turn(client, session=ANA, history=history, message="And the order?")
    response = next(
        u for u in updates if u["type"] == "event" and u["event"]["kind"] == "tool_response"
    )
    assert json.loads(response["event"]["content"])["refunded"] == 20
    assert updates[0]["index"] == 2


def test_turn_validates_the_message(client: TestClient) -> None:
    assert client.post("/api/conversation/turn", json={"message": ""}).status_code == 422


def test_unavailable_without_live_mode(settings: Settings) -> None:
    client = TestClient(create_app(settings))  # replay backend, not live
    setup = client.get("/api/conversation").json()
    assert setup["available"] is False
    assert "pac serve --live" in setup["error"]
    response = client.post("/api/conversation/turn", json={"message": "hi"})
    assert response.status_code == 503
