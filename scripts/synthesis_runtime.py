"""Wiring for scripts/synthesize.py: model backends, the ledger, the response cache, file I/O.

Every model call goes  CachingBackend -> LedgeredBackend -> provider backend:
the cache (data/synthetic/llm_cache) makes reruns free, the ledger counts and caps only the
calls that reach a provider. One LedgeredBackend instance per model, shared by every role that
model plays, so MiniMax's concurrency limit (2) holds across the jury, the classifier and the
agent at once.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel

from pac.guardrail.application.llm_classifier import LLMClassifier
from pac.guardrail.application.ports import Completion, LLMBackend, LLMRequest
from pac.guardrail.domain.policy import Policy
from pac.guardrail.infrastructure.files import load_policy, load_prompt
from pac.guardrail.infrastructure.llm.anthropic import AnthropicBackend
from pac.guardrail.infrastructure.llm.caching import CachingBackend
from pac.settings import REPO_ROOT, Settings
from pac.synthesis.application.labeller import Juror, JuryLabeller
from pac.synthesis.domain.specs import applies_to_from_policy
from pac.synthesis.infrastructure.ledger import Cap, Ledger, LedgeredBackend, Price
from pac.synthesis.infrastructure.openrouter import OpenRouterBackend
from pac.synthesis.infrastructure.retry import RetryingBackend

logger = logging.getLogger("pac.scripts.synthesize")

OUT = REPO_ROOT / "data" / "synthetic"
CACHE = OUT / "llm_cache"
LEDGER = OUT / "ledger.json"
PRICES = OUT / "prices.json"
CLASSIFIER_PROMPT = REPO_ROOT / "prompts" / "classifier_v2.md"

# The budget (hard caps). MiniMax counts calls; the others dollars at list price.
CAPS = {
    "minimax": Cap(calls=6000),
    "anthropic": Cap(usd=5.00),
    "openrouter": Cap(usd=3.50),
}
# Internal stops a little under the caps, so a phase in flight can't cross them.
SOFT_CAPS = {
    "minimax": Cap(calls=5900),
    "anthropic": Cap(usd=4.75),
    "openrouter": Cap(usd=3.30),
}

MINIMAX = "MiniMax-M3"
# MiniMax's own API allows about 3 calls at once (HTTP 429 on 23% of calls at 4), which made it
# the bottleneck of every phase. The same model is served through OpenRouter instead.
MINIMAX_VIA_OPENROUTER = "minimax/minimax-m3"
CLAUDE = "anthropic/claude-haiku-5.5"
CLAUDE_STRONG = "anthropic/claude-sonnet-5.5"
GPT = "openai/gpt-6-luna"
LLAMA = "meta-llama/llama-4-scout"
MISTRAL = "mistralai/mistral-small-3.2-24b-instruct"
QWEN = "qwen/qwen3.7-flash"
DEEPSEEK = "deepseek/deepseek-v4-flash"

FAMILY = {
    MINIMAX: "minimax",
    CLAUDE: "anthropic-claude",
    CLAUDE_STRONG: "anthropic-claude",
    GPT: "openai-gpt",
    LLAMA: "meta-llama",
    MISTRAL: "mistral",
    QWEN: "qwen",
    DEEPSEEK: "deepseek",
}

# Claude on the Anthropic API directly (list prices), used when the key can reach a workspace.
ANTHROPIC_PRICES = {
    "claude-haiku-5-5": Price(input=0.10, output=0.50, cache_read=0.01, cache_write=0.125),
    "claude-sonnet-5-5": Price(input=2.00, output=10.00, cache_read=0.20, cache_write=2.50),
}
# OpenRouter request extras: reasoning effort low where a model reasons.
OPENROUTER_EXTRA: dict[str, dict[str, Any]] = {
    GPT: {"reasoning": {"effort": "low"}},
    # Thinking off for Haiku: it doubled output tokens on the jury prompt (probe, 2026-10-12).
    CLAUDE: {"reasoning": {"enabled": False}},
    CLAUDE_STRONG: {"reasoning": {"effort": "low"}},
    # Qwen 3.7 Flash thinks by default and ran out of the customer's 512 tokens.
    QWEN: {"reasoning": {"enabled": False}},
    DEEPSEEK: {"reasoning": {"enabled": False}},
    # Thinking off, as on MiniMax's API; MiniMax's own endpoint first, other fp8 hosts under load.
    MINIMAX_VIA_OPENROUTER: {
        "reasoning": {"enabled": False},
        "provider": {"order": ["minimax"], "quantizations": ["fp8", "bf16"]},
    },
}


class _Renamed:
    """A backend answering under another model id, so cache keys and ledger rows keep that id."""

    def __init__(self, inner: OpenRouterBackend, model: str) -> None:
        self._inner = inner
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    def reported_cost(self, completion: Completion) -> float | None:
        return self._inner.reported_cost(completion)

    async def complete(self, request: LLMRequest) -> Completion:
        return await self._inner.complete(request)


def openrouter_prices(models: Iterable[str], settings: Settings) -> dict[str, Price]:
    """List prices from OpenRouter's models API, cached in data/synthetic/prices.json."""
    wanted = set(models)
    cached: dict[str, Any] = json.loads(PRICES.read_text()) if PRICES.exists() else {}
    if not wanted <= set(cached):
        response = httpx.get(f"{settings.openrouter_base_url}/models", timeout=60)
        response.raise_for_status()
        for model in response.json()["data"]:
            if model["id"] in wanted:
                p = model.get("pricing", {})
                cached[model["id"]] = {
                    "input": float(p.get("prompt", 0)) * 1e6,
                    "output": float(p.get("completion", 0)) * 1e6,
                    # Priced as uncached input: conservative; the key's real usage is checked.
                    "cache_read": float(p.get("prompt", 0)) * 1e6,
                }
        PRICES.parent.mkdir(parents=True, exist_ok=True)
        PRICES.write_text(json.dumps(cached, indent=1, sort_keys=True) + "\n")
    return {m: Price(**cached[m]) for m in wanted}


def openrouter_key_usage(settings: Settings) -> float:
    """Dollars the OpenRouter key has spent, from the provider (never prints the key)."""
    key = settings.openrouter_api_key
    if key is None:
        raise RuntimeError("OPENROUTER_API_KEY is not set")
    response = httpx.get(
        f"{settings.openrouter_base_url}/auth/key",
        headers={"Authorization": f"Bearer {key.get_secret_value()}"},
        timeout=30,
    )
    response.raise_for_status()
    return float(response.json()["data"]["usage"])


@dataclass
class Runtime:
    settings: Settings
    ledger: Ledger
    policy: Policy
    _ledgered: dict[str, LedgeredBackend] = field(default_factory=dict)
    _prices: dict[str, Price] = field(default_factory=dict)

    @classmethod
    def create(cls) -> Runtime:
        settings = Settings()
        ledger = Ledger.load(LEDGER, SOFT_CAPS)
        return cls(settings=settings, ledger=ledger, policy=load_policy(settings.policy_path))

    @property
    def applies_to(self) -> dict[str, tuple[Any, ...]]:
        return dict(applies_to_from_policy(self.policy.text))

    def provider_of(self, model: str) -> str:
        if model == MINIMAX:
            return "minimax"
        if model in ANTHROPIC_PRICES:
            return "anthropic"
        return "openrouter"

    def raw(self, model: str) -> LedgeredBackend:
        """The ledgered (uncached) backend for a model, shared by every role."""
        if model in self._ledgered:
            return self._ledgered[model]
        s = self.settings
        provider = self.provider_of(model)
        inner: LLMBackend
        price: Price | None = None
        if provider == "minimax":
            # On MiniMax's API, 2 at a time left every other juror idle (25.6 events/min
            # labelling) and 4 drew HTTP 429s on 23% of calls, so MiniMax-M3 now goes through
            # OpenRouter, still named MiniMax-M3 so its cached answers stay valid. Spend is
            # counted against the OpenRouter budget.
            if s.openrouter_api_key is None:
                raise RuntimeError("OPENROUTER_API_KEY is not set")
            via = MINIMAX_VIA_OPENROUTER
            inner = _Renamed(
                OpenRouterBackend(
                    via,
                    base_url=s.openrouter_base_url,
                    api_key=s.openrouter_api_key.get_secret_value(),
                    extra_body=OPENROUTER_EXTRA[via],
                ),
                model,
            )
            if via not in self._prices:
                self._prices |= openrouter_prices([via], s)
            price = self._prices[via]
            provider = "openrouter"
            concurrency = 32
        elif provider == "anthropic":
            if s.anthropic_api_key is None:
                raise RuntimeError("ANTHROPIC_API_KEY is not set")
            inner = AnthropicBackend(
                model, api_key=s.anthropic_api_key.get_secret_value(), effort="low", timeout_s=120
            )
            price = ANTHROPIC_PRICES[model]
            concurrency = 6
        else:
            if s.openrouter_api_key is None:
                raise RuntimeError("OPENROUTER_API_KEY is not set")
            inner = OpenRouterBackend(
                model,
                base_url=s.openrouter_base_url,
                api_key=s.openrouter_api_key.get_secret_value(),
                extra_body=OPENROUTER_EXTRA.get(model, {}),
            )
            if model not in self._prices:
                self._prices |= openrouter_prices([model], s)
            price = self._prices[model]
            concurrency = 6
        backend = LedgeredBackend(
            inner, self.ledger, provider=provider, price=price, concurrency=concurrency
        )
        self._ledgered[model] = backend
        return backend

    def cached(self, model: str, namespace: str = "") -> CachingBackend:
        """A model behind the response cache; ``namespace`` separates repeated runs."""
        directory = CACHE / namespace if namespace else CACHE
        return CachingBackend(directory, inner=RetryingBackend(self.raw(model)))

    def jury(self, models: Sequence[str] = (MINIMAX, CLAUDE, GPT)) -> JuryLabeller:
        jurors = [Juror(name=m, family=FAMILY[m], backend=self.cached(m)) for m in models]
        return JuryLabeller(jurors, self.policy.text, self.applies_to)

    def classifier(self, run: int = 1) -> LLMClassifier:
        """The target: prompt v2 with MiniMax-M3, as deployed (provider-default sampling).

        Each run has its own cache namespace, so run 2 is a fresh call, not a cache hit.
        """
        prompt = load_prompt(CLASSIFIER_PROMPT, self.policy, max_tokens=1024)
        backend = self.cached(MINIMAX, namespace=f"classifier-run{run}")
        return LLMClassifier(backend, self.policy, prompt, timeout_s=180)


def cache_entries() -> dict[str, int]:
    """Successful calls per model, counted from the response cache (one file per call)."""
    counts: dict[str, int] = {}
    for model in FAMILY:
        safe = model.replace("/", "_")
        counts[model] = sum(1 for _ in CACHE.glob(f"**/{safe}/*/*.json"))
    return counts


def reconcile(runtime: Runtime) -> dict[str, int]:
    """Add calls the cache proves were made but the ledger lacks (two processes once wrote the
    ledger at the same time, before it was file-locked), priced at the model's average."""
    data = runtime.ledger.as_dict()
    added: dict[str, int] = {}
    for model, cached in cache_entries().items():
        rows = [r for r in data["rows"] if r["model"] == model]
        logged = sum(r["calls"] - r["errors"] for r in rows)
        if cached > logged:
            calls = sum(r["calls"] for r in rows) or 1
            avg = sum(r["usd"] for r in rows) / calls
            provider = runtime.provider_of(model)
            with runtime.ledger.phase("unlogged-recovered-from-cache"):
                runtime.ledger.recover(provider, model, cached - logged, avg * (cached - logged))
            added[model] = cached - logged
    return added


M = TypeVar("M", bound=BaseModel)


def write_jsonl(path: Path, rows: Iterable[BaseModel | dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            data = row.model_dump(mode="json") if isinstance(row, BaseModel) else row
            fh.write(json.dumps(data, ensure_ascii=False) + "\n")
            n += 1
    return n


def read_jsonl[M: BaseModel](path: Path, model: type[M]) -> list[M]:
    if not path.exists():
        return []
    return [
        model.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
