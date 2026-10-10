"""On-disk response cache, as a decorator around any backend.

The cache is committed to the repo. That makes every reported number reproducible
byte-for-byte, and lets a colleague without an API key replay the eval and use the demo.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from pac.backends.base import BackendError, Completion, LLMBackend, LLMRequest


class CacheMiss(BackendError):
    """Raised in replay mode when a request has never been recorded."""


def _safe(model: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", model)


class CachingBackend:
    """Reads recorded completions; on a miss, delegates to ``inner`` (or raises in replay mode).

    The key covers the model id and the full request (system prompt, user prompt, schema,
    token limit), so any change to the policy or prompt naturally invalidates old entries.
    """

    def __init__(self, cache_dir: Path, *, inner: LLMBackend | None, model: str | None = None):
        if inner is None and model is None:
            raise ValueError("replay mode needs the model id whose recordings to read")
        self._dir = cache_dir
        self._inner = inner
        self._model = model or (inner.model if inner else "")

    @property
    def model(self) -> str:
        return self._model

    def _path(self, key: str) -> Path:
        return self._dir / _safe(self._model) / key[:2] / f"{key}.json"

    async def complete(self, request: LLMRequest) -> Completion:
        key = request.cache_key(self._model)
        path = self._path(key)
        if path.exists():
            record = json.loads(path.read_text(encoding="utf-8"))
            return Completion.model_validate(record["completion"]).model_copy(
                update={"cached": True}
            )
        if self._inner is None:
            raise CacheMiss(f"no recorded completion for this request under model {self._model}")

        completion = await self._inner.complete(request)
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {"user": request.user, "completion": completion.model_dump(mode="json")}
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(record, indent=1, sort_keys=True), encoding="utf-8")
        tmp.replace(path)
        return completion
