"""The composition root wires the right adapters for each configuration."""

from __future__ import annotations

from pathlib import Path

import pytest

from pac.bootstrap import build_classifier
from pac.guardrail.application.ports import BackendError
from pac.guardrail.infrastructure.llm.caching import CachingBackend
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
