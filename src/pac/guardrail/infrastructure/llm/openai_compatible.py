"""Any provider exposing the OpenAI chat-completions API: OpenAI, OpenRouter, MiniMax, vLLM, ..."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from pac.guardrail.application.ports import BackendError, Completion, LLMRequest
from pac.guardrail.domain.classification import Usage

logger = logging.getLogger(__name__)


class OpenAICompatibleBackend:
    """Calls ``POST {base_url}/chat/completions``.

    The output schema is stated in the system prompt rather than sent as ``response_format``,
    because support for JSON-schema constrained decoding varies across providers (MiniMax does
    not document it). ``extra_body`` carries provider-specific switches, set by the composition
    root: for MiniMax-M3 they disable thinking (latency on the request path) and split any
    reasoning into its own field so it never pollutes the JSON answer.
    """

    def __init__(
        self,
        model: str,
        *,
        base_url: str,
        api_key: str,
        extra_body: dict[str, Any] | None = None,
        timeout_s: float = 60.0,
        client: httpx.AsyncClient | None = None,
        max_rate_limit_retries: int = 5,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._model = model
        self._extra_body = extra_body or {}
        self._max_rate_limit_retries = max_rate_limit_retries
        self._sleep = sleep
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout_s,
            headers={"Authorization": f"Bearer {api_key}"},
        )

    @property
    def model(self) -> str:
        return self._model

    async def complete(self, request: LLMRequest) -> Completion:
        system: Any = request.system
        if self._model.startswith("anthropic/"):
            # Claude through OpenRouter: mark the system prompt (most of it is the policy) for
            # prompt caching, so repeat calls read it at a tenth of the input price.
            system = [
                {"type": "text", "text": request.system, "cache_control": {"type": "ephemeral"}}
            ]
        payload = {
            "model": self._model,
            "max_completion_tokens": request.max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": request.user},
            ],
            **self._extra_body,
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        for attempt in range(self._max_rate_limit_retries + 1):
            # Timed per attempt, so waiting out a rate limit never counts as model latency.
            started = time.perf_counter()
            try:
                response = await self._client.post("/chat/completions", json=payload)
            except httpx.HTTPError as exc:
                raise BackendError(f"{self._model} request failed: {exc!r}") from exc
            if response.status_code != 429 or attempt == self._max_rate_limit_retries:
                break
            wait = _backoff(response, attempt)
            logger.warning(
                "rate limited; waiting",
                extra={"model": self._model, "wait_s": wait, "retry": attempt + 1},
            )
            await self._sleep(wait)
        try:
            response.raise_for_status()
            body = response.json()
        except httpx.HTTPStatusError as exc:
            raise BackendError(
                f"{self._model} HTTP {exc.response.status_code}: {exc.response.text[:200]}"
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise BackendError(f"{self._model} request failed: {exc!r}") from exc

        try:
            choice = body["choices"][0]
            text = choice["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise BackendError(f"{self._model} response had no message content") from exc
        if choice.get("finish_reason") == "length":
            raise BackendError(f"{self._model} output hit max tokens before completing")
        if not isinstance(text, str) or not text.strip():
            raise BackendError(f"{self._model} returned empty content")

        usage = body.get("usage") or {}
        cached_tokens = (usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
        return Completion(
            text=text,
            model=self._model,
            latency_ms=(time.perf_counter() - started) * 1000,
            usage=Usage(
                # OpenAI-style prompt_tokens include cached tokens; Anthropic's input_tokens
                # do not. Normalise to the Anthropic meaning so reports are comparable.
                input_tokens=int(usage.get("prompt_tokens", 0)) - int(cached_tokens),
                output_tokens=int(usage.get("completion_tokens", 0)),
                cache_read_input_tokens=int(cached_tokens),
            ),
        )


def _backoff(response: httpx.Response, attempt: int) -> float:
    """Seconds to wait after a 429: the server's Retry-After, else 2, 4, 8, ... (max 60)."""
    retry_after = response.headers.get("retry-after")
    if retry_after is not None:
        try:
            return min(max(float(retry_after), 0.0), 60.0)
        except ValueError:
            pass
    return float(min(2 ** (attempt + 1), 60))
