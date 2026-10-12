"""The call and cost ledger, with hard budget caps.

Every model call that reaches a provider (cache hits don't) is recorded by phase, provider and
model: calls, tokens, and dollars at list price from the tokens the API reports. A provider's
cap is checked before each call; a call that would go past it raises ``BudgetExceeded`` instead
of being made. The ledger is a JSON file, re-read and rewritten under a file lock after every
call, so several processes can share it and a crash loses nothing.

Wrap order: ``CachingBackend(inner=LedgeredBackend(provider backend))``, so only real calls are
counted and limited.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import fcntl
import json
import os
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pac.guardrail.application.ports import (
    Completion,
    LLMBackend,
    LLMRequest,
    NonRetryableBackendError,
)
from pac.synthesis.infrastructure.openrouter import ReportsCost

_PHASE: contextvars.ContextVar[str] = contextvars.ContextVar("synthesis_phase", default="unphased")


class BudgetExceeded(NonRetryableBackendError):
    """A call would go past a provider's cap. Not retried: the run must stop and report."""


@dataclass(frozen=True)
class Price:
    """US dollars per million tokens."""

    input: float
    output: float
    cache_read: float = 0.0
    cache_write: float = 0.0

    def cost(self, completion: Completion) -> float:
        u = completion.usage
        return (
            u.input_tokens * self.input
            + u.output_tokens * self.output
            + u.cache_read_input_tokens * self.cache_read
            + u.cache_creation_input_tokens * self.cache_write
        ) / 1e6


@dataclass(frozen=True)
class Cap:
    """A provider's budget: a number of calls, or dollars, or both."""

    calls: int | None = None
    usd: float | None = None


@dataclass
class _Line:
    calls: int = 0
    errors: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    usd: float = 0.0


@dataclass
class Ledger:
    path: Path
    caps: dict[str, Cap]
    lines: dict[tuple[str, str, str], _Line] = field(default_factory=dict)
    notes: list[dict[str, Any]] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @classmethod
    def load(cls, path: Path, caps: dict[str, Cap]) -> Ledger:
        ledger = cls(path=path, caps=caps)
        ledger._reload()
        return ledger

    def _reload(self) -> None:
        """Replace the in-memory lines with the file's: another process may have added calls."""
        self.lines = {}
        self.notes = []
        if not self.path.exists():
            return
        data = json.loads(self.path.read_text(encoding="utf-8"))
        for row in data.get("rows", []):
            key = (row["phase"], row["provider"], row["model"])
            self.lines[key] = _Line(**{k: row[k] for k in _Line.__dataclass_fields__ if k in row})
        self.notes = list(data.get("notes", []))

    @contextlib.contextmanager
    def _exclusive(self) -> Iterator[None]:
        """Hold the ledger across threads and processes, reloaded from the file."""
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.with_suffix(".lock").open("a+") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                try:
                    self._reload()
                    yield
                finally:
                    fcntl.flock(lock, fcntl.LOCK_UN)

    @staticmethod
    @contextlib.contextmanager
    def phase(name: str) -> Iterator[None]:
        token = _PHASE.set(name)
        try:
            yield
        finally:
            _PHASE.reset(token)

    def spent(self, provider: str) -> tuple[int, float]:
        rows = [line for (_, p, _), line in self.lines.items() if p == provider]
        return sum(r.calls for r in rows), sum(r.usd for r in rows)

    def check(self, provider: str) -> None:
        cap = self.caps.get(provider)
        if cap is None:
            return
        with self._exclusive():
            calls, usd = self.spent(provider)
        if cap.calls is not None and calls + 1 > cap.calls:
            raise BudgetExceeded(f"{provider}: {calls} calls made, cap {cap.calls}")
        if cap.usd is not None and usd >= cap.usd:
            raise BudgetExceeded(f"{provider}: ${usd:.4f} spent, cap ${cap.usd:.2f}")

    def record(self, provider: str, model: str, completion: Completion | None, usd: float) -> None:
        with self._exclusive():
            line = self.lines.setdefault((_PHASE.get(), provider, model), _Line())
            line.calls += 1
            if completion is None:
                line.errors += 1
            else:
                u = completion.usage
                line.input_tokens += u.input_tokens
                line.output_tokens += u.output_tokens
                line.cache_read_tokens += u.cache_read_input_tokens
                line.cache_write_tokens += u.cache_creation_input_tokens
                line.usd += usd
            self._save()

    def recover(self, provider: str, model: str, calls: int, usd: float) -> None:
        """Count calls known (from the response cache) to have been made but never recorded."""
        with self._exclusive():
            line = self.lines.setdefault((_PHASE.get(), provider, model), _Line())
            line.calls += calls
            line.usd += usd
            self._save()

    def note(self, **fields: Any) -> None:
        with self._exclusive():
            self.notes.append({"phase": _PHASE.get(), "at": round(time.time()), **fields})
            self._save()

    def as_dict(self) -> dict[str, Any]:
        rows = [
            {"phase": ph, "provider": p, "model": m, **vars(line)}
            for (ph, p, m), line in sorted(self.lines.items())
        ]
        totals: dict[str, dict[str, float]] = {}
        for row in rows:
            t = totals.setdefault(row["provider"], {"calls": 0, "usd": 0.0})
            t["calls"] += row["calls"]
            t["usd"] = round(t["usd"] + row["usd"], 6)
        for row in rows:
            row["usd"] = round(row["usd"], 6)
        caps = {p: {"calls": c.calls, "usd": c.usd} for p, c in self.caps.items()}
        return {"caps": caps, "totals": totals, "rows": rows, "notes": self.notes}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(self.as_dict(), indent=1) + "\n", encoding="utf-8")
        tmp.replace(self.path)


class LedgeredBackend:
    """Counts and limits the calls that reach a provider; at most ``concurrency`` at once."""

    def __init__(
        self,
        inner: LLMBackend,
        ledger: Ledger,
        *,
        provider: str,
        price: Price | None = None,
        concurrency: int = 4,
    ) -> None:
        self._inner = inner
        self._ledger = ledger
        self._provider = provider
        self._price = price
        self._semaphore = asyncio.Semaphore(concurrency)

    @property
    def model(self) -> str:
        return self._inner.model

    async def complete(self, request: LLMRequest) -> Completion:
        async with self._semaphore:
            self._ledger.check(self._provider)
            try:
                completion = await self._inner.complete(request)
            except BudgetExceeded:
                raise
            except Exception:
                self._ledger.record(self._provider, self.model, None, 0.0)
                raise
        reported = (
            self._inner.reported_cost(completion) if isinstance(self._inner, ReportsCost) else None
        )
        if reported is not None:
            usd = reported
        else:
            usd = self._price.cost(completion) if self._price is not None else 0.0
        self._ledger.record(self._provider, self.model, completion, usd)
        return completion
