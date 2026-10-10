"""LLM backends and the factory that builds one from settings."""

from __future__ import annotations

from pac.backends.anthropic_backend import AnthropicBackend
from pac.backends.base import BackendError, Completion, LLMBackend, LLMRequest
from pac.backends.cache import CacheMiss, CachingBackend
from pac.backends.ollama_backend import OllamaBackend
from pac.settings import Settings

__all__ = [
    "BackendError",
    "CacheMiss",
    "CachingBackend",
    "Completion",
    "LLMBackend",
    "LLMRequest",
    "build_backend",
]


def build_backend(settings: Settings, *, use_cache: bool = True) -> LLMBackend:
    """Build the configured backend, wrapped in the response cache.

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
    else:
        inner = OllamaBackend(
            settings.model, host=settings.ollama_host, timeout_s=settings.timeout_s
        )

    return CachingBackend(settings.cache_dir, inner=inner) if use_cache else inner
