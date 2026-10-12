"""Runtime configuration, read from the environment (prefix ``PAC_``) or a ``.env`` file."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]

# "openai" is any OpenAI-compatible chat-completions endpoint (vLLM, Together, Groq, OpenAI, ...);
# "minimax" and "openrouter" are presets of it with their base URL and key already wired.
BackendName = Literal["anthropic", "openai", "minimax", "openrouter", "ollama", "replay"]


class Settings(BaseSettings):
    """All tunables in one place. Nothing here is a secret except the API key."""

    model_config = SettingsConfigDict(env_prefix="PAC_", env_file=".env", extra="ignore")

    # Default: serve the committed recordings, so a fresh clone works with no keys or model.
    # Set PAC_BACKEND / PAC_MODEL (see .env.example) to classify new events live.
    backend: BackendName = "replay"
    model: str = "qwen3.5"

    anthropic_api_key: SecretStr | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    minimax_api_key: SecretStr | None = Field(default=None, validation_alias="MINIMAX_API_KEY")
    minimax_base_url: str = "https://api.minimax.io/v1"
    # Thinking off by default: a guardrail on the request path cannot wait for long reasoning.
    minimax_thinking: Literal["disabled", "adaptive"] = "disabled"
    openrouter_api_key: SecretStr | None = Field(
        default=None, validation_alias="OPENROUTER_API_KEY"
    )
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openai_api_key: SecretStr | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    openai_base_url: str = "https://api.openai.com/v1"
    # Provider-specific request fields for the generic "openai" backend, as JSON, e.g.
    # PAC_OPENAI_EXTRA_BODY='{"reasoning_effort": "low"}'.
    openai_extra_body: dict[str, Any] = Field(default_factory=dict)
    ollama_host: str = "http://localhost:11434"

    # Live mode: every classification calls the model; the response cache is neither read nor
    # written. For demos -- eval runs should keep the cache so results stay reproducible.
    live: bool = False

    policy_path: Path = REPO_ROOT / "policy" / "policy_v0.md"
    prompt_path: Path = REPO_ROOT / "prompts" / "classifier_v2.md"
    cases_path: Path = REPO_ROOT / "data" / "eval" / "dev_test.jsonl"
    hard_cases_path: Path = REPO_ROOT / "data" / "eval" / "hard.jsonl"
    stress_cases_path: Path = REPO_ROOT / "data" / "eval" / "stress.jsonl"
    agents_cases_path: Path = REPO_ROOT / "data" / "eval" / "agents.jsonl"
    # Generated cases for prompt optimisation only, never for reporting.
    train_cases_path: Path = REPO_ROOT / "data" / "train" / "train_cases.jsonl"
    examples_path: Path = REPO_ROOT / "data" / "ui" / "demo_examples.json"
    # The live conversation's simulated agent: its instructions, and an optional different
    # model to play it (same backend). Unset, the classifier's model plays both roles.
    agent_prompt_path: Path = REPO_ROOT / "prompts" / "lark_agent.md"
    agent_model: str | None = None
    cache_dir: Path = REPO_ROOT / "cache"
    results_dir: Path = REPO_ROOT / "results"
    web_dist: Path = REPO_ROOT / "web" / "dist"

    # Classification is a short, latency-sensitive call; "low" effort is the documented
    # sweet spot for that workload on current Claude models.
    anthropic_effort: Literal["low", "medium", "high"] = "low"

    # Drift monitoring: the reference profile live traffic is compared with (built by
    # `pac monitor reference`), the rolling window, and how many events before judging drift.
    monitoring_reference_path: Path = REPO_ROOT / "results" / "monitoring" / "reference.json"
    monitoring_window: int = 500
    monitoring_min_events: int = 100

    # Logging: level (DEBUG shows every classification) and text or one-JSON-object-per-line.
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["text", "json"] = "text"

    timeout_s: float = 60.0
    max_output_tokens: int = 1024
    # Sampling temperature for the classifier. Unset leaves the provider's default (MiniMax
    # samples; Ollama is pinned to 0). Set it, e.g. PAC_TEMPERATURE=0, for repeatable runs.
    temperature: float | None = None
    concurrency: int = 4

    @field_validator(
        "anthropic_api_key", "minimax_api_key", "openrouter_api_key", "openai_api_key", mode="after"
    )
    @classmethod
    def _blank_key_is_no_key(cls, value: SecretStr | None) -> SecretStr | None:
        # `MINIMAX_API_KEY=` in a copied .env must read as "not configured", not as a key.
        return value if value is not None and value.get_secret_value().strip() else None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
