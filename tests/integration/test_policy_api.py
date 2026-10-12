"""Integration: editing the policy in the app changes what the classifier judges by."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pac.guardrail.application.llm_classifier import LLMClassifier
from pac.guardrail.application.ports import LLMRequest
from pac.guardrail.application.prompting import PromptTemplate
from pac.guardrail.domain.events import ClassificationRequest, TraceEvent
from pac.guardrail.domain.policy import Policy
from pac.interfaces.api import create_app
from pac.settings import BackendName, Settings
from tests.support.builders import decision_json
from tests.support.fakes import FakeBackend

pytestmark = pytest.mark.integration

NEW_RULE = """

**R14 — No talk of competitors.** Lark must not mention another retailer by name.
*Applies to: model_output.*
"""


def _responder(request: LLMRequest) -> str:
    # The fake model only knows about R14 if the policy in its prompt has it.
    if "R14" in request.system + request.user and "Voltmart" in request.user:
        return decision_json("violation", ["R14"])
    return decision_json("allow")


def _settings(tmp_path: Path, backend: BackendName) -> Settings:
    return Settings(
        backend=backend,
        results_dir=tmp_path / "results",
        web_dist=tmp_path / "no-dist",
        monitoring_reference_path=tmp_path / "reference.json",
    )


def _client(settings: Settings, policy: Policy, prompt: PromptTemplate) -> TestClient:
    classifier = LLMClassifier(FakeBackend(_responder), policy, prompt)
    return TestClient(create_app(settings, classifier))


def _verdict(client: TestClient) -> tuple[str, list[str]]:
    event = TraceEvent(kind="model_output", content="Voltmart sells it cheaper, honestly.")
    body = ClassificationRequest(event=event).model_dump(mode="json")
    decision = client.post("/api/classify", json=body).json()["decision"]
    return decision["verdict"], decision["rule_ids"]


def test_edit_applies_to_the_classifier_and_reset_restores_it(
    tmp_path: Path, policy: Policy, prompt: PromptTemplate
) -> None:
    client = _client(_settings(tmp_path, "openrouter"), policy, prompt)
    shown = client.get("/api/policy").json()
    assert (shown["edited"], shown["locked"]) == (False, None)
    assert _verdict(client) == ("allow", [])

    edited = client.put("/api/policy", json={"text": shown["text"] + NEW_RULE}).json()
    assert edited["edited"] is True
    assert "R14" in edited["rule_ids"]
    assert _verdict(client) == ("violation", ["R14"])

    reset = client.delete("/api/policy").json()
    assert reset["edited"] is False
    assert reset["sha256"] == shown["sha256"]
    assert _verdict(client) == ("allow", [])


def test_rejects_text_that_is_not_a_policy(
    tmp_path: Path, policy: Policy, prompt: PromptTemplate
) -> None:
    client = _client(_settings(tmp_path, "openrouter"), policy, prompt)
    assert client.put("/api/policy", json={"text": "no rules here"}).status_code == 422
    assert client.get("/api/policy").json()["edited"] is False


def test_replay_mode_cannot_edit(tmp_path: Path, policy: Policy, prompt: PromptTemplate) -> None:
    client = _client(_settings(tmp_path, "replay"), policy, prompt)
    shown = client.get("/api/policy").json()
    assert "Replay mode" in shown["locked"]
    response = client.put("/api/policy", json={"text": shown["text"] + NEW_RULE})
    assert response.status_code == 409
