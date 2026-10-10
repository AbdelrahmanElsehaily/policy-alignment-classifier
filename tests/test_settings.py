import pytest

from pac.settings import REPO_ROOT, Settings


def test_defaults_point_inside_repo() -> None:
    settings = Settings()
    assert settings.policy_path.is_relative_to(REPO_ROOT)
    assert settings.cases_path.is_relative_to(REPO_ROOT)


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAC_BACKEND", "ollama")
    monkeypatch.setenv("PAC_MODEL", "qwen3.5")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    settings = Settings()
    assert settings.backend == "ollama"
    assert settings.model == "qwen3.5"
    assert settings.anthropic_api_key is not None
    assert settings.anthropic_api_key.get_secret_value() == "sk-test"
