"""OpenRouter chat completions, with prompt caching for Claude and the provider's own cost.

The guardrail's OpenAI-compatible adapter covers OpenRouter, but a synthesis run makes
thousands of calls with the same long system prompt (the policy and the labelling guide), so
two things matter here that don't on the request path: Anthropic models on OpenRouter cache a
prompt only when its blocks carry ``cache_control``, and OpenRouter reports what each call
actually cost (``usage.cost``), which the ledger records instead of an estimate.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Protocol, runtime_checkable

import httpx

from pac.guardrail.application.ports import BackendError, Completion, LLMRequest
from pac.guardrail.domain.classification import Usage

logger = logging.getLogger(__name__)


@runtime_checkable
class ReportsCost(Protocol):
    def reported_cost(self, completion: Completion) -> float | None: ...


class OpenRouterBackend:
    def __init__(
        self,
        model: str,
        *,
        api_key: str,
        base_url: str = "https://openrouter.ai/api/v1",
        extra_body: dict[str, Any] | None = None,
        timeout_s: float = 120.0,
        client: httpx.AsyncClient | None = None,
        max_retries: int = 4,
    ) -> None:
        self._model = model
        self._extra = extra_body or {}
        self._max_retries = max_retries
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout_s,
            headers={"Authorization": f"Bearer {api_key}"},
        )
        self._costs: dict[int, float] = {}

    @property
    def model(self) -> str:
        return self._model

    def reported_cost(self, completion: Completion) -> float | None:
        return self._costs.pop(id(completion), None)

    def _system(self, text: str) -> Any:
        if self._model.startswith("anthropic/"):
            return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]
        return text

    async def complete(self, request: LLMRequest) -> Completion:
        payload: dict[str, Any] = {
            "model": self._model,
            "max_tokens": request.max_tokens,
            "messages": [
                {"role": "system", "content": self._system(request.system)},
                {"role": "user", "content": request.user},
            ],
            "usage": {"include": True},
            **self._extra,
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        response: httpx.Response | None = None
        started = time.perf_counter()
        for attempt in range(self._max_retries + 1):
            started = time.perf_counter()
            try:
                response = await self._client.post("/chat/completions", json=payload)
            except httpx.HTTPError as exc:
                if attempt == self._max_retries:
                    raise BackendError(f"{self._model} request failed: {exc!r}") from exc
                await asyncio.sleep(2 ** (attempt + 1))
                continue
            if response.status_code in (429, 500, 502, 503, 504) and attempt < self._max_retries:
                await asyncio.sleep(min(2 ** (attempt + 1), 30))
                continue
            break
        assert response is not None
        if response.status_code != 200:
            raise BackendError(f"{self._model} HTTP {response.status_code}: {response.text[:200]}")
        try:
            body = response.json()
            choice = body["choices"][0]
            text = choice["message"].get("content")
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise BackendError(f"{self._model} response had no message content") from exc
        if choice.get("finish_reason") == "length":
            raise BackendError(f"{self._model} output hit max tokens before completing")
        if not isinstance(text, str) or not text.strip():
            raise BackendError(f"{self._model} returned empty content")
        usage = body.get("usage") or {}
        cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
        completion = Completion(
            text=text,
            model=self._model,
            latency_ms=(time.perf_counter() - started) * 1000,
            usage=Usage(
                input_tokens=int(usage.get("prompt_tokens", 0)) - int(cached),
                output_tokens=int(usage.get("completion_tokens", 0)),
                cache_read_input_tokens=int(cached),
            ),
        )
        cost = usage.get("cost")
        if isinstance(cost, int | float):
            self._costs[id(completion)] = float(cost)
        return completion
