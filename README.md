# Policy alignment classifier

An LLM-prompted classifier that reads **one agent trace event** (`user_input`, `model_output`, `tool_call` or
`tool_response`) and decides whether a written policy permits it. It returns `allow`, `violation` or
`policy_silent`, with the rules it relied on, a rationale and a confidence.

The deployment is **Lark**, a customer-support agent for *Larkspur Electronics*, a fictional retailer. The
policy, the eval set, the corner cases and the reasoning behind every choice are in this repo.

**Start with [`docs/WRITEUP.md`](docs/WRITEUP.md)**, which covers results, assumptions, corner cases and
limitations.

## Run it

```bash
uv run pac serve
```

This installs Python dependencies, builds the React app on first run (needs Node.js 22.12+), and serves the
UI and API on <http://127.0.0.1:8000>.

**No API key needed.** By default (`PAC_BACKEND=replay`) the app answers from the **recorded** model
responses committed in `cache/`. Those cover every eval case (dev and test, as the UI sends them) and the
position-demo presets. An event that was never recorded is not guessed at. It fails closed and shows as
"error → human review", and the regex baseline still answers. To classify *new* events live, pick a
backend:

| Backend | Set | Notes |
|---|---|---|
| MiniMax-M3 | `PAC_BACKEND=minimax PAC_MODEL=MiniMax-M3 MINIMAX_API_KEY=…` | OpenAI-compatible API; thinking disabled for latency |
| Claude | `PAC_BACKEND=anthropic PAC_MODEL=claude-haiku-5-5 ANTHROPIC_API_KEY=…` | Structured outputs plus prompt caching of the policy |
| Local (Ollama) | `PAC_BACKEND=ollama PAC_MODEL=qwen3.5` | The model behind every committed result (each run's `config.json` records its backend, prompt hash and commit) |
| Recorded only (default) | `PAC_BACKEND=replay PAC_MODEL=qwen3.5` | Offline; serves the committed cache, and unseen events fail closed |

Copy [`.env.example`](.env.example) to `.env` to set these.

## Evaluate

```bash
uv run pac check-data                                  # validate the eval set against the policy
uv run pac eval --split dev                            # iterate here
uv run pac eval --split test                           # the reported numbers
uv run pac eval --split test --classifier baseline     # flat-text regex baseline
uv run pac eval --split test --with-history            # ablation: give the classifier prior events
uv run pac eval --split test --no-cache --repeats 3    # run-to-run variance
```

Each run writes `results/runs/<run_id>/` containing `config.json` (prompt, policy and dataset hashes, git
commit), `predictions.jsonl`, `metrics.json` and a readable `report.md`. Every proportion carries a 95%
Wilson interval.

## Develop

```bash
uv sync                          # Python env (3.14)
uv run pytest                    # unit + integration tests, offline
uv run ruff format . && uv run ruff check . && uv run mypy && uv run lint-imports
cd web && npm ci && npm test && npm run build      # Node.js 22.12+ (CI uses 24)
```

CI ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) enforces formatting, lint, strict typing (Python
and TypeScript), the architecture's dependency rules (import-linter), and the tests. It also **replays every committed eval run from the response cache and
requires identical verdicts and metrics**, so a change that silently moves a reported number fails the
build.

## Repository map

| Path | What |
|---|---|
| [`policy/policy_v0.md`](policy/policy_v0.md) | The policy the classifier is measured against (frozen) |
| [`policy/policy_v1_proposed.md`](policy/policy_v1_proposed.md) | Amendments the corner cases argue for |
| [`prompts/`](prompts/) | Versioned prompt templates (v1 kept for traceability; v2 is the default) |
| [`data/build_cases.py`](data/build_cases.py) | The eval set, with each label's reasoning; generates `data/cases.jsonl` |
| [`src/pac/guardrail/`](src/pac/guardrail/) | Core domain: judging one event. Domain model, the LLM classifier use case, model adapters, regex baseline |
| [`src/pac/evaluation/`](src/pac/evaluation/) | Measuring a guardrail: labelled cases, metrics, eval use cases, run storage |
| [`src/pac/interfaces/`](src/pac/interfaces/), [`bootstrap.py`](src/pac/bootstrap.py) | CLI and API, and the composition root that wires them |
| [`cache/`](cache/) | Recorded model responses, which make every reported number reproducible offline |
| [`results/runs/`](results/runs/) | Committed eval runs |
| [`web/`](web/) | One-page React app |
| [`docs/WRITEUP.md`](docs/WRITEUP.md) | Reasoning, assumptions, results, limitations |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Where the guardrail sits (man in the middle), bounded contexts, layers, enforced dependency rules |
| [`docs/CORNER_CASES.md`](docs/CORNER_CASES.md) | What the policy does not decide, and what to do about it |
| [`docs/LABELING.md`](docs/LABELING.md) | Labelling conventions and who labelled |
| [`docs/POLICY_RATIONALE.md`](docs/POLICY_RATIONALE.md) | Why each rule exists and is worded as it is |
| [`docs/AI_OVERRIDES.md`](docs/AI_OVERRIDES.md) | Where AI-produced work was overridden |
