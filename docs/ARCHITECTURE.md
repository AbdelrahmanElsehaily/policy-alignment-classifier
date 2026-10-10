# Architecture

## Where the guardrail sits

The guardrail is a **man in the middle**. It observes the events passing between an agent and its users
and tools, and returns a verdict for each one (`allow`, `violation`, or `policy_silent`, which means
escalate to a human). It does **not** control the agent: it can't change the agent's prompts, tools, tool
schemas or platform flows, and it can't add data to the agent's tool calls. Everything it knows comes
from what passes through it. Two consequences shape the design:

- **Missing facts must be recovered from the stream, not requested from the agent.** A refund call doesn't
  carry the order's total or delivery date, but the `lookup_order` response that preceded it passed
  through the guardrail. A stateful guardrail can remember it. The history ablation measures exactly this
  (see [`WRITEUP.md`](WRITEUP.md)).
- **The session envelope is an assumption about what the guardrail can observe.** The classifier is given
  `verified`, `verified_customer_id` and `email_on_file` with every event. In a deployment, that requires
  the guardrail to see the platform's verification outcome, or to derive it from events it has already seen
  (for example, the verification step completing). If it can see neither, R1, R2 and R7 become
  undecidable. This is stated as an assumption, not a solved problem.

## Bounded contexts

```
                ┌───────────────────────────── interfaces ────────────────────────────┐
                │   cli.py  (pac …)                      api.py  (FastAPI + React UI) │
                └───────────────────────────────────┬─────────────────────────────────┘
                                                    │
                              bootstrap.py  (composition root: Settings → objects)
                                                    │
        ┌───────────────── evaluation ──────────────┴───┐   ┌──────────────── guardrail ─────────────────┐
        │ infrastructure  case_store · run_store ·      │   │ infrastructure  llm/{anthropic, openai_     │
        │                 provenance                    │   │                 compatible, ollama,         │
        │ application     run_eval · reporting · ports  │   │                 caching} · regex_classifier │
        │ domain          cases · prediction · run ·    │──▶│                 · files                     │
        │                 metrics · agreement · pricing │   │ application     llm_classifier · prompting  │
        └───────────────────────────────────────────────┘   │                 · ports (LLMBackend)        │
                                                            │ domain          events · decision ·         │
                                                            │                 classification · policy     │
                                                            └─────────────────────────────────────────────┘
```

- **`guardrail`** is the core domain: judging one trace event against a policy. It doesn't know evaluation
  exists.
- **`evaluation`** is a supporting domain: measuring a guardrail on labelled cases. It is a downstream
  consumer that uses only the guardrail's *domain model* (events, verdicts, the `EventClassifier`
  contract, `Policy`), never its application or infrastructure.

## Layers within each context

| Layer | Holds | May depend on |
|---|---|---|
| `domain` | Value objects and pure rules: `TraceEvent`, `Decision`, `Policy`, `LabeledCase`, metrics | Nothing outside the domain (pydantic is used for validation only) |
| `application` | Use cases and the ports they need: `LLMClassifier`, prompting (the anti-corruption layer to the LLM), `run_cases` / `record_run`, `LLMBackend` and `RunStore` ports | Its own domain |
| `infrastructure` | Adapters that implement the ports: LLM providers, the response cache, the regex classifier, file and JSONL stores, git | Application and domain |
| `bootstrap` | Reads `Settings` and wires adapters into use cases | Everything below it |
| `interfaces` | CLI and HTTP API: parse input, call bootstrap and use cases, format output | Everything below it |

**Enforced, not aspirational.** These rules are import-linter contracts in `pyproject.toml`, run by
`uv run lint-imports` and in CI. Domain and application code may not import `anthropic`, `httpx`,
`fastapi`, `typer`, `uvicorn` or `pac.settings`.

## Key seams

| Port | Implementations | Why it is a seam |
|---|---|---|
| `EventClassifier` (domain) | `LLMClassifier`, `RegexBaseline` | Evaluation and the API run any guardrail without knowing which one |
| `LLMBackend` (guardrail application) | Anthropic, OpenAI-compatible (MiniMax), Ollama, `CachingBackend`, test fake | Swapping models is configuration. The cache is a decorator, so every backend gets record and replay. |
| `NonRetryableBackendError` (guardrail application) | `CacheMiss` | The classifier's retry policy depends on the kind of error, not on a specific adapter |
| `RunStore` (evaluation application) | `FileRunStore` | Use cases persist runs without knowing about files. Run-id validation (no path traversal) lives in the adapter. |

## Tests mirror the structure

```
tests/unit/guardrail/{domain,application,infrastructure}/   one module under test, fakes at the ports
tests/unit/evaluation/{domain,infrastructure}/
tests/integration/   API, CLI and the eval pipeline, several layers together, offline
tests/artifacts/     the committed dataset, eval runs and demo presets must stay valid and reproducible
tests/support/       FakeBackend and builders
```
