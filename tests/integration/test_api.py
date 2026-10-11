"""Integration: the HTTP API with an injected fake-backed classifier and a temp results dir."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pac.evaluation.application.run_eval import record_run, run_cases
from pac.evaluation.domain.cases import LabeledCase
from pac.evaluation.domain.run import RunConfig
from pac.evaluation.infrastructure.run_store import FileRunStore
from pac.guardrail.application.llm_classifier import LLMClassifier
from pac.guardrail.application.ports import LLMRequest
from pac.guardrail.application.prompting import PromptTemplate
from pac.guardrail.domain.events import ClassificationRequest, TraceEvent
from pac.guardrail.domain.policy import Policy
from pac.interfaces.api import API_VERSION, create_app
from pac.settings import Settings
from tests.support.builders import decision_json
from tests.support.fakes import FakeBackend

pytestmark = pytest.mark.integration


def _responder(request: LLMRequest) -> str:
    if "4539" in request.user and '"model_output"' in request.user:
        return decision_json("violation", ["R3"])
    return decision_json("allow")


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(results_dir=tmp_path / "results", web_dist=tmp_path / "no-dist")


@pytest.fixture
def client(settings: Settings, policy: Policy, prompt: PromptTemplate) -> TestClient:
    classifier = LLMClassifier(FakeBackend(_responder), policy, prompt)
    return TestClient(create_app(settings, classifier))


def _body(kind: str, content: str) -> dict[str, object]:
    event = TraceEvent.model_validate({"kind": kind, "content": content})
    return ClassificationRequest(event=event).model_dump(mode="json")


def test_health_and_policy(client: TestClient) -> None:
    health = client.get("/api/health").json()
    assert health["llm_available"] is True
    assert health["api_version"] == API_VERSION
    policy = client.get("/api/policy").json()
    assert policy["version"] == "v0"
    assert policy["rule_ids"][:2] == ["R1", "R2"]
    assert "Larkspur" in policy["text"]


def test_cases_endpoint_serves_dataset(client: TestClient) -> None:
    cases = client.get("/api/cases").json()
    assert len(cases) >= 150
    assert {"id", "request", "label", "rationale"} <= set(cases[0])


def test_classify_llm_and_baseline(client: TestClient) -> None:
    card = "4539 1488 0343 6467"
    llm_user = client.post("/api/classify", json=_body("user_input", card)).json()
    llm_model = client.post("/api/classify", json=_body("model_output", card)).json()
    base_user = client.post(
        "/api/classify", params={"engine": "baseline"}, json=_body("user_input", card)
    ).json()

    assert llm_user["decision"]["verdict"] == "allow"
    assert llm_model["decision"]["verdict"] == "violation"
    assert base_user["decision"]["verdict"] == "violation"
    assert base_user["model"] == "regex-baseline"


def test_classify_validates_input(client: TestClient) -> None:
    response = client.post("/api/classify", json={"event": {"kind": "tool_call"}})
    assert response.status_code == 422


def test_unconfigured_llm_returns_503(tmp_path: Path) -> None:
    settings = Settings(backend="anthropic", web_dist=tmp_path, results_dir=tmp_path)
    client = TestClient(create_app(settings))
    assert client.get("/api/health").json()["llm_available"] is False
    response = client.post("/api/classify", json=_body("user_input", "hi"))
    assert response.status_code == 503
    assert "ANTHROPIC_API_KEY" in response.json()["detail"]
    baseline = client.post(
        "/api/classify", params={"engine": "baseline"}, json=_body("user_input", "hi")
    )
    assert baseline.status_code == 200


async def test_runs_endpoints(
    client: TestClient, settings: Settings, policy: Policy, prompt: PromptTemplate
) -> None:
    assert client.get("/api/runs").json() == []
    cases = client.get("/api/cases").json()[:3]
    labeled = [LabeledCase.model_validate(c) for c in cases]
    predictions = await run_cases(LLMClassifier(FakeBackend(_responder), policy, prompt), labeled)
    run_id = "20261010T000000Z_fake_dev"
    config = RunConfig(
        run_id=run_id,
        created_at="2026-10-10T00:00:00+00:00",
        classifier="fake-model",
        backend="fake",
        prompt_version=prompt.version,
        policy_version="v0",
        policy_sha256=policy.sha256,
        dataset_sha256="0" * 64,
        split="dev",
        n_cases=3,
        repeats=1,
        with_history=False,
    )
    record_run(FileRunStore(settings.results_dir / "runs"), config, predictions)

    summaries = client.get("/api/runs").json()
    assert [s["run_id"] for s in summaries] == [run_id]
    summary = summaries[0]
    assert summary["accuracy"]["n"] == 3
    assert summary["dataset"] == "cases.jsonl"
    assert {"violation_recall", "false_block_rate", "silent_recall", "classifier_errors"} <= set(
        summary
    )
    detail = client.get(f"/api/runs/{run_id}").json()
    assert len(detail["predictions"]) == 3
    assert detail["metrics"]["n_cases"] == 3
    assert client.get("/api/runs/nope").status_code == 404
    assert client.get("/api/runs/..%2F..%2Fetc").status_code == 404
    assert client.get("/api/runs/%2E%2E").status_code == 404
    assert client.get("/api/runs/.hidden").status_code == 404


def test_root_without_frontend(client: TestClient) -> None:
    assert "not built" in client.get("/").json()["message"]


def test_serves_built_frontend(tmp_path: Path, policy: Policy, prompt: PromptTemplate) -> None:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>pac</title>")
    (dist / "assets" / "app.js").write_text("console.log(1)")
    settings = Settings(web_dist=dist, results_dir=tmp_path)
    client = TestClient(
        create_app(settings, LLMClassifier(FakeBackend(_responder), policy, prompt))
    )
    assert "<title>pac</title>" in client.get("/").text
    assert client.get("/assets/app.js").status_code == 200


def test_parent_dir_run_id_cannot_escape(
    tmp_path: Path, policy: Policy, prompt: PromptTemplate
) -> None:
    """A results dir whose parent looks like a run must still not be served via '..'."""
    (tmp_path / "config.json").write_text("{}")
    settings = Settings(results_dir=tmp_path, web_dist=tmp_path / "none")
    client = TestClient(
        create_app(settings, LLMClassifier(FakeBackend(_responder), policy, prompt))
    )
    (tmp_path / "runs").mkdir()
    assert client.get("/api/runs/%2E%2E").status_code == 404


def test_blank_api_key_means_unconfigured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MINIMAX_API_KEY", "")
    settings = Settings(backend="minimax", web_dist=tmp_path, results_dir=tmp_path)
    health = TestClient(create_app(settings)).get("/api/health").json()
    assert health["llm_available"] is False
    assert "MINIMAX_API_KEY" in health["llm_error"]


def test_examples_and_live_flag(client: TestClient) -> None:
    examples = client.get("/api/examples").json()
    assert len(examples) >= 10
    assert {"title", "look_for", "case"} <= set(examples[0])
    assert client.get("/api/health").json()["live"] is False
