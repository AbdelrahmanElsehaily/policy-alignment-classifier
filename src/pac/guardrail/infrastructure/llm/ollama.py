"""A local model served by Ollama, using its JSON-schema constrained decoding."""

from __future__ import annotations

import time

import httpx

from pac.guardrail.application.ports import BackendError, Completion, LLMRequest
from pac.guardrail.domain.classification import Usage


class OllamaBackend:
    """Calls ``POST /api/chat`` with ``format`` set to the output schema.

    Thinking is disabled and temperature pinned to 0: a guardrail on the request path cannot
    afford minutes of hidden reasoning, and eval runs should be as repeatable as possible.
    """

    def __init__(
        self,
        model: str,
        *,
        host: str = "http://localhost:11434",
        timeout_s: float = 120.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._model = model
        self._client = client or httpx.AsyncClient(base_url=host, timeout=timeout_s)

    @property
    def model(self) -> str:
        return self._model

    async def complete(self, request: LLMRequest) -> Completion:
        payload = {
            "model": self._model,
            "stream": False,
            "think": False,
            "format": request.json_schema,
            "options": {
                "temperature": 0 if request.temperature is None else request.temperature,
                "num_predict": request.max_tokens,
            },
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.user},
            ],
        }
        started = time.perf_counter()
        try:
            response = await self._client.post("/api/chat", json=payload)
            response.raise_for_status()
            body = response.json()
        except httpx.HTTPStatusError as exc:
            raise BackendError(
                f"ollama HTTP {exc.response.status_code}: {exc.response.text[:200]}"
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise BackendError(f"ollama request failed: {exc!r}") from exc

        if not isinstance(body, dict):
            raise BackendError("ollama response was not a JSON object")
        if body.get("done_reason") == "length":
            raise BackendError("ollama output hit num_predict before completing")
        try:
            text = body["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise BackendError("ollama response had no message content") from exc

        return Completion(
            text=text,
            model=self._model,
            latency_ms=(time.perf_counter() - started) * 1000,
            usage=Usage(
                input_tokens=int(body.get("prompt_eval_count", 0)),
                output_tokens=int(body.get("eval_count", 0)),
            ),
        )
