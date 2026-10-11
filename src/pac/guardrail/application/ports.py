"""What the guardrail application needs from the outside world: an LLM.

The classifier depends only on :class:`LLMBackend`; swapping Claude for MiniMax, a local
model, a recorded cache, or a test double is a constructor argument, not a code change.
Adapters live in ``pac.guardrail.infrastructure.llm``.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from pac.guardrail.domain.classification import Usage


class LLMRequest(BaseModel):
    """A provider-neutral structured-output request."""

    model_config = ConfigDict(frozen=True)

    system: str
    user: str
    json_schema: dict[str, Any]
    max_tokens: int
    # None leaves sampling to the provider's default.
    temperature: float | None = None

    def cache_key(self, model: str) -> str:
        fields = self.model_dump(exclude={"temperature"})
        # Only a set temperature joins the key, so recordings made without one keep their keys.
        if self.temperature is not None:
            fields["temperature"] = self.temperature
        payload = json.dumps({"model": model, **fields}, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()


class Completion(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: str
    model: str
    usage: Usage = Usage()
    # Wall time of the provider call when it was made. Preserved through the cache, so a
    # replayed run still reports the latency the model actually had.
    latency_ms: float = 0.0
    cached: bool = False


class BackendError(RuntimeError):
    """Any failure to obtain a completion. The classifier turns these into fail-closed verdicts."""


class NonRetryableBackendError(BackendError):
    """A failure that the same request cannot fix (e.g. no recording exists in replay mode)."""


@runtime_checkable
class LLMBackend(Protocol):
    @property
    def model(self) -> str: ...

    async def complete(self, request: LLMRequest) -> Completion: ...
