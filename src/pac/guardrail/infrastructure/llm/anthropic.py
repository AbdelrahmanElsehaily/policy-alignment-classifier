"""Claude via the official Anthropic SDK, with structured outputs and prompt caching."""

from __future__ import annotations

import time
from typing import Any, Literal

import anthropic

from pac.guardrail.application.ports import BackendError, Completion, LLMRequest
from pac.guardrail.domain.classification import Usage

Effort = Literal["low", "medium", "high"]


class AnthropicBackend:
    """Calls the Messages API with a JSON-schema output format.

    The system prompt (role + full policy) is identical for every event, so it carries a
    ``cache_control`` breakpoint: after the first call, the policy is billed at the cache-read
    rate. Effort defaults to ``low`` because this sits on the request path.
    """

    def __init__(
        self,
        model: str,
        *,
        client: anthropic.AsyncAnthropic | None = None,
        api_key: str | None = None,
        effort: Effort = "low",
        timeout_s: float = 60.0,
    ) -> None:
        self._model = model
        self._effort = effort
        self._client = client or anthropic.AsyncAnthropic(api_key=api_key, timeout=timeout_s)

    @property
    def model(self) -> str:
        return self._model

    async def complete(self, request: LLMRequest) -> Completion:
        started = time.perf_counter()
        try:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=request.max_tokens,
                system=[
                    {
                        "type": "text",
                        "text": request.system,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": request.user}],
                output_config=self._output_config(request.json_schema),
            )
        except anthropic.APIConnectionError as exc:
            raise BackendError(f"anthropic connection error: {exc}") from exc
        except anthropic.APIStatusError as exc:
            raise BackendError(f"anthropic HTTP {exc.status_code}: {exc.message}") from exc

        if response.stop_reason == "refusal":
            raise BackendError("anthropic refused to classify this event")
        if response.stop_reason == "max_tokens":
            raise BackendError("anthropic output hit max_tokens before completing")

        text = next((block.text for block in response.content if block.type == "text"), None)
        if text is None:
            raise BackendError("anthropic response contained no text block")

        usage = response.usage
        return Completion(
            text=text,
            model=self._model,
            latency_ms=(time.perf_counter() - started) * 1000,
            usage=Usage(
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_input_tokens=usage.cache_read_input_tokens or 0,
                cache_creation_input_tokens=usage.cache_creation_input_tokens or 0,
            ),
        )

    def _output_config(self, schema: dict[str, Any]) -> Any:
        return {"effort": self._effort, "format": {"type": "json_schema", "schema": schema}}
