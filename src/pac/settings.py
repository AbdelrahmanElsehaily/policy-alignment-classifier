"""Runtime configuration, read from the environment (prefix ``PAC_``) or a ``.env`` file."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]

BackendName = Literal["anthropic", "ollama", "replay", "fake"]


class Settings(BaseSettings):
    """All tunables in one place. Nothing here is a secret except the API key."""

    model_config = SettingsConfigDict(env_prefix="PAC_", env_file=".env", extra="ignore")

    backend: BackendName = "anthropic"
    model: str = "claude-haiku-5-5"

    anthropic_api_key: SecretStr | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    ollama_host: str = "http://localhost:11434"

    policy_path: Path = REPO_ROOT / "policy" / "policy_v0.md"
    prompt_path: Path = REPO_ROOT / "prompts" / "classifier_v1.md"
    cases_path: Path = REPO_ROOT / "data" / "cases.jsonl"
    cache_dir: Path = REPO_ROOT / "cache"
    results_dir: Path = REPO_ROOT / "results"
    web_dist: Path = REPO_ROOT / "web" / "dist"

    timeout_s: float = 60.0
    max_output_tokens: int = 1024
    concurrency: int = 4


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
