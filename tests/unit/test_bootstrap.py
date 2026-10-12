"""The composition root wires the right adapters for each configuration."""

from __future__ import annotations

from pathlib import Path

import pytest

from pac.bootstrap import (
    build_agent,
    build_classifier,
    dataset_file,
    dataset_path,
    expected_cases,
    expected_profile,
    load_all_cases,
)
from pac.guardrail.application.ports import BackendError
from pac.guardrail.infrastructure.llm.caching import CachingBackend
from pac.guardrail.infrastructure.llm.ollama import OllamaBackend
from pac.guardrail.infrastructure.llm.openai_compatible import OpenAICompatibleBackend
from pac.settings import Settings


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    return Settings(cache_dir=tmp_path, **overrides)  # type: ignore[arg-type]


def test_default_wraps_the_backend_in_the_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MINIMAX_API_KEY", "k")
    classifier = build_classifier(_settings(tmp_path, backend="minimax", model="MiniMax-M3"))
    assert isinstance(classifier._backend, CachingBackend)


def test_live_mode_calls_the_model_directly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MINIMAX_API_KEY", "k")
    classifier = build_classifier(
        _settings(tmp_path, backend="minimax", model="MiniMax-M3", live=True)
    )
    assert isinstance(classifier._backend, OpenAICompatibleBackend)


def test_live_mode_refuses_replay(tmp_path: Path) -> None:
    with pytest.raises(BackendError, match="Live mode needs a real model"):
        build_classifier(_settings(tmp_path, backend="replay", live=True))


def test_the_agent_needs_live_mode_and_a_real_model(tmp_path: Path) -> None:
    with pytest.raises(BackendError, match="needs a real model in live mode"):
        build_agent(_settings(tmp_path, backend="ollama"))
    with pytest.raises(BackendError, match="needs a real model in live mode"):
        build_agent(_settings(tmp_path, backend="replay", live=True))


def test_the_agent_is_never_cached_and_can_use_its_own_model(tmp_path: Path) -> None:
    agent = build_agent(_settings(tmp_path, backend="ollama", live=True, agent_model="llama3.3"))
    assert isinstance(agent._backend, OllamaBackend)
    assert agent.model == "llama3.3"
    assert "You are Lark" in agent._system
    same_model = build_agent(_settings(tmp_path, backend="ollama", live=True, model="qwen3.5"))
    assert same_model.model == "qwen3.5"


def test_each_split_reads_its_own_dataset_file(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    assert dataset_path(settings, "hard") == settings.stress_cases_path
    assert dataset_path(settings, "stress") == settings.stress_cases_path
    assert dataset_path(settings, "train") == settings.train_cases_path
    assert dataset_path(settings, "test") == settings.cases_path
    assert dataset_path(settings, None) == settings.cases_path


def test_all_cases_spans_both_files_with_unique_ids() -> None:
    cases = load_all_cases(Settings())
    assert {c.split for c in cases} == {"dev", "test", "stress", "agents", "train"}
    assert len({c.id for c in cases}) == len(cases)


def test_runs_find_their_dataset_by_current_or_former_name() -> None:
    settings = Settings()
    assert dataset_file(settings, "dev_test.jsonl") == settings.cases_path
    assert dataset_file(settings, "cases.jsonl") == settings.cases_path
    assert dataset_file(settings, "stress_cases.jsonl") == settings.stress_cases_path
    with pytest.raises(FileNotFoundError):
        dataset_file(settings, "nope.jsonl")


def test_the_drift_reference_is_test_plus_recorded_conversations() -> None:
    settings = Settings()
    cases = expected_cases(settings)
    assert {c.split for c in cases} == {"test", "stress"}
    assert all("family:recorded" in c.tags for c in cases if c.split == "stress")
    assert expected_profile(settings).events == len(cases)
