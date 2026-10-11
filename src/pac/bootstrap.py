"""Composition root: the only place that turns ``Settings`` into wired objects.

Interfaces (CLI, API) ask this module for ready-to-use services; nothing below the interface
layer reads configuration or picks concrete adapters.
"""

from __future__ import annotations

from pathlib import Path

from pac.evaluation.domain.cases import LabeledCase, Split, ensure_unique_ids
from pac.evaluation.infrastructure.case_store import load_cases
from pac.evaluation.infrastructure.run_store import FileRunStore
from pac.guardrail.application.llm_classifier import LLMClassifier
from pac.guardrail.application.ports import BackendError, LLMBackend
from pac.guardrail.domain.policy import Policy
from pac.guardrail.infrastructure.files import load_policy, load_prompt
from pac.guardrail.infrastructure.llm.anthropic import AnthropicBackend
from pac.guardrail.infrastructure.llm.caching import CachingBackend
from pac.guardrail.infrastructure.llm.ollama import OllamaBackend
from pac.guardrail.infrastructure.llm.openai_compatible import OpenAICompatibleBackend
from pac.guardrail.infrastructure.regex_classifier import RegexBaseline
from pac.settings import Settings
from pac.simulation.application.agent import SimulatedAgent


def build_backend(settings: Settings, *, use_cache: bool = True) -> LLMBackend:
    """The configured LLM backend, wrapped in the response cache.

    ``replay`` serves only recorded completions and never touches the network.
    """
    if settings.backend == "replay":
        return CachingBackend(settings.cache_dir, inner=None, model=settings.model)

    inner: LLMBackend
    if settings.backend == "anthropic":
        if settings.anthropic_api_key is None:
            raise BackendError(
                "ANTHROPIC_API_KEY is not set. Set it, or use PAC_BACKEND=replay to serve "
                "recorded results, or PAC_BACKEND=ollama for a local model."
            )
        inner = AnthropicBackend(
            settings.model,
            api_key=settings.anthropic_api_key.get_secret_value(),
            effort=settings.anthropic_effort,
            timeout_s=settings.timeout_s,
        )
    elif settings.backend == "minimax":
        if settings.minimax_api_key is None:
            raise BackendError("MINIMAX_API_KEY is not set.")
        inner = OpenAICompatibleBackend(
            settings.model,
            base_url=settings.minimax_base_url,
            api_key=settings.minimax_api_key.get_secret_value(),
            extra_body={
                "thinking": {"type": settings.minimax_thinking},
                "reasoning_split": True,
            },
            timeout_s=settings.timeout_s,
        )
    else:
        inner = OllamaBackend(
            settings.model, host=settings.ollama_host, timeout_s=settings.timeout_s
        )

    return CachingBackend(settings.cache_dir, inner=inner) if use_cache else inner


def build_policy(settings: Settings) -> Policy:
    return load_policy(settings.policy_path)


def build_classifier(settings: Settings, backend: LLMBackend | None = None) -> LLMClassifier:
    """The LLM classifier: configured backend + policy + prompt.

    In live mode the backend is not wrapped in the cache, so every call reaches the model.
    """
    if backend is None and settings.live:
        if settings.backend == "replay":
            raise BackendError(
                "Live mode needs a real model: set PAC_BACKEND to ollama, minimax or anthropic."
            )
        backend = build_backend(settings, use_cache=False)
    policy = build_policy(settings)
    prompt = load_prompt(settings.prompt_path, policy, max_tokens=settings.max_output_tokens)
    return LLMClassifier(
        backend if backend is not None else build_backend(settings),
        policy,
        prompt,
        timeout_s=settings.timeout_s,
    )


def build_baseline(settings: Settings) -> RegexBaseline:
    return RegexBaseline(policy_version=build_policy(settings).version)


def build_run_store(settings: Settings) -> FileRunStore:
    return FileRunStore(settings.results_dir / "runs")


def build_agent(settings: Settings) -> SimulatedAgent:
    """The simulated agent for the live conversation. Never cached: every turn is new.

    It needs live mode, which also keeps the classifier off the response cache, so a chat never
    writes recordings next to the committed eval cache.
    """
    if not settings.live or settings.backend == "replay":
        raise BackendError(
            "The live conversation needs a real model in live mode: set PAC_BACKEND to "
            "ollama, minimax or anthropic and start with `pac serve --live`."
        )
    if settings.agent_model is not None:
        settings = settings.model_copy(update={"model": settings.agent_model})
    return SimulatedAgent(
        build_backend(settings, use_cache=False),
        settings.agent_prompt_path.read_text(),
        max_tokens=settings.max_output_tokens,
    )


def dataset_path(settings: Settings, split: Split | None) -> Path:
    """The file holding a split: hard and stress have their own; dev and test share one."""
    if split == "hard":
        return settings.hard_cases_path
    if split == "stress":
        return settings.stress_cases_path
    return settings.cases_path


def load_all_cases(settings: Settings) -> list[LabeledCase]:
    """Every labelled case from every dataset file, with ids unique across files."""
    cases = load_cases(settings.cases_path)
    for extra in (settings.hard_cases_path, settings.stress_cases_path):
        if extra.exists():
            cases += load_cases(extra)
    ensure_unique_ids(cases)
    return cases
