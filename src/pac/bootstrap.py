"""Composition root: the only place that turns ``Settings`` into wired objects.

Interfaces (CLI, API) ask this module for ready-to-use services; nothing below the interface
layer reads configuration or picks concrete adapters.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import SecretStr

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
from pac.monitoring.application.monitor import Monitor
from pac.monitoring.domain.drift import Profile
from pac.monitoring.domain.features import observe, observe_expected
from pac.monitoring.infrastructure.exporters import load_profile
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
    elif settings.backend in ("openai", "minimax", "openrouter"):
        base_url, key, key_name, extra_body = _openai_compatible(settings)
        if key is None:
            raise BackendError(f"{key_name} is not set.")
        inner = OpenAICompatibleBackend(
            settings.model,
            base_url=base_url,
            api_key=key.get_secret_value(),
            extra_body=extra_body,
            timeout_s=settings.timeout_s,
        )
    else:
        inner = OllamaBackend(
            settings.model, host=settings.ollama_host, timeout_s=settings.timeout_s
        )

    return CachingBackend(settings.cache_dir, inner=inner) if use_cache else inner


def _openai_compatible(
    settings: Settings,
) -> tuple[str, SecretStr | None, str, dict[str, Any]]:
    """Base URL, API key, the key's variable name, and extra request fields for an
    OpenAI-compatible provider. Only the presets know provider-specific switches."""
    if settings.backend == "minimax":
        # Thinking off (latency on the request path); any reasoning kept out of the answer.
        thinking = {"thinking": {"type": settings.minimax_thinking}, "reasoning_split": True}
        return settings.minimax_base_url, settings.minimax_api_key, "MINIMAX_API_KEY", thinking
    if settings.backend == "openrouter":
        return settings.openrouter_base_url, settings.openrouter_api_key, "OPENROUTER_API_KEY", {}
    return (
        settings.openai_base_url,
        settings.openai_api_key,
        "OPENAI_API_KEY",
        settings.openai_extra_body,
    )


def build_policy(settings: Settings) -> Policy:
    return load_policy(settings.policy_path)


def build_classifier(settings: Settings, backend: LLMBackend | None = None) -> LLMClassifier:
    """The LLM classifier: configured backend + policy + prompt.

    In live mode the backend is not wrapped in the cache, so every call reaches the model.
    """
    if backend is None and settings.live:
        if settings.backend == "replay":
            raise BackendError(
                "Live mode needs a real model: set PAC_BACKEND to ollama, openai, openrouter, "
                "minimax or anthropic."
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
            "ollama, openai, openrouter, minimax or anthropic and start with `pac serve --live`."
        )
    if settings.agent_model is not None:
        settings = settings.model_copy(update={"model": settings.agent_model})
    return SimulatedAgent(
        build_backend(settings, use_cache=False),
        settings.agent_prompt_path.read_text(),
        max_tokens=settings.max_output_tokens,
    )


def dataset_path(settings: Settings, split: Split | None) -> Path:
    """The file holding a split: dev and test share one; every later split has its own."""
    if split == "hard":
        return settings.hard_cases_path
    if split == "stress":
        return settings.stress_cases_path
    if split == "agents":
        return settings.agents_cases_path
    return settings.cases_path


# Dataset file names recorded by runs made before data/ was reorganised.
_FORMER_NAMES = {
    "cases.jsonl": "dev_test.jsonl",
    "hard_cases.jsonl": "hard.jsonl",
    "stress_cases.jsonl": "stress.jsonl",
}


def dataset_file(settings: Settings, name: str) -> Path:
    """The dataset a run measured, from the file name in its config (current or former)."""
    wanted = _FORMER_NAMES.get(name, name)
    for path in (
        settings.cases_path,
        settings.hard_cases_path,
        settings.stress_cases_path,
        settings.agents_cases_path,
    ):
        if path.name == wanted:
            return path
    raise FileNotFoundError(f"no dataset file named {name}")


def load_all_cases(settings: Settings) -> list[LabeledCase]:
    """Every labelled case from every dataset file, with ids unique across files."""
    cases = load_cases(settings.cases_path)
    for extra in (settings.hard_cases_path, settings.stress_cases_path, settings.agents_cases_path):
        if extra.exists():
            cases += load_cases(extra)
    ensure_unique_ids(cases)
    return cases


def build_monitor(settings: Settings) -> Monitor:
    """The live drift monitor, with the reference profile if one has been built."""
    return Monitor(
        load_profile(settings.monitoring_reference_path),
        window=settings.monitoring_window,
        min_events=settings.monitoring_min_events,
    )


def expected_cases(settings: Settings) -> list[LabeledCase]:
    """The traffic the classifier is expected to see: the test split and the events of the
    recorded natural conversations. Dev (tuned on), hard and the generated stress families
    (built to over-represent corner cases) are left out."""
    test = [c for c in load_cases(settings.cases_path) if c.split == "test"]
    recorded = [c for c in load_cases(settings.stress_cases_path) if "family:recorded" in c.tags]
    return test + recorded


def expected_profile(settings: Settings) -> Profile:
    """The reference for drift: expected traffic with its gold labels, independent of any model.

    Events are profiled as the evaluated classifier sees them, one at a time without history, and
    with their single-event labels; traffic that sends history then shows up as different.
    """
    return Profile.of(
        observe_expected(c.request.without_history(), c.label, c.rule_ids)
        for c in expected_cases(settings)
    )


def profile_of_run(settings: Settings, run_id: str) -> Profile:
    """The monitoring profile of a committed run: what it saw and what it said.

    Raises ``RunNotFound`` for an unknown run.
    """
    saved = build_run_store(settings).load(run_id)
    by_id = {c.id: c for c in load_all_cases(settings)}
    with_history = saved.config.with_history
    return Profile.of(
        observe(by_id[p.case_id].request_for(with_history=with_history), p.result)
        for p in saved.predictions
        if p.repeat == 0 and p.case_id in by_id
    )
