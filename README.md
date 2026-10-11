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

## Live demo (real model calls)

```bash
PAC_BACKEND=ollama PAC_MODEL=qwen3.5 uv run pac serve --live
```

`--live` sends **every request to the model**. The response cache is neither read nor written, so even
known eval cases get a fresh answer. For MiniMax, use `PAC_BACKEND=minimax PAC_MODEL=MiniMax-M3
MINIMAX_API_KEY=… uv run pac serve --live`. The server refuses `--live` in replay mode rather than
silently serving recordings.

The UI opens on **Examples**: 14 curated scenarios from the eval set, each showing the expected verdict and
what to look for. **Run** asks the live model and shows its verdict, rationale and latency next to the
regex baseline. **Run with history** (on the refund and address-change cases) shows the earlier events
settling a case the single event can't decide. **Run all** goes through them one at a time, which is about
2–3 minutes on a laptop with the local 9.7B model. The banner says LIVE or RECORDED, and every result is
marked "live" or "recorded".

**Live conversation** lets you chat with Lark yourself. Lark is a simulated agent (an LLM with a fake
back office seeded with the eval set's customers and orders), and the guardrail sits inline: your message,
each tool call, each tool result and each reply waits for its verdict. `allow` passes, `violation` is
**blocked**, and `policy_silent` is **held** for a human. A stopped event is replaced by the guardrail's
stand-in: an error Lark can react to, or a notice for you. A blocked refund never reaches the back
office. The regex baseline is shown alongside and never blocks. Pick the session (unverified, or verified as Ana or Ben) and start from one
of the ten scenarios, which each aim at a rule or a documented corner case, or type freely. Toggle
whether the guardrail sees the conversation so far.

On one local model, the agent and the guardrail share the GPU and evict each other's cached prompt, so
each event waits 20–40 s for its verdict. Set `PAC_AGENT_MODEL` to let a different model play Lark (same backend), or use MiniMax for both.

## Evaluate

```bash
uv run pac check-data                                  # validate the eval set against the policy
uv run pac eval --split dev                            # iterate here
uv run pac eval --split test                           # the reported numbers
uv run pac eval --split test --classifier baseline     # flat-text regex baseline
uv run pac eval --split test --with-history            # ablation: give the classifier prior events
uv run pac eval --split test --no-cache --repeats 3    # run-to-run variance
uv run pac eval --split hard --with-history            # round 2: hard real-world cases
```

The `hard` split (31 events, in `data/hard_cases.jsonl`) was written after v1.0 and labelled before any
model ran on it. It holds the cases a real deployment meets and the policy doesn't decide: a refund
retried after a timeout, euros against a dollar cap, IMEIs that look like card numbers, gift card codes,
publishable keys, planted strings that trigger blocks, and more. See
[`docs/CORNER_CASES.md`](docs/CORNER_CASES.md#round-2-the-hard-split-after-v10).

Each run writes `results/runs/<run_id>/` containing `config.json` (prompt, policy and dataset hashes, git
commit), `predictions.jsonl`, `metrics.json` and a readable `report.md`. Every proportion carries a 95%
Wilson interval.

## Monitor drift

Production traffic has no labels, so accuracy can't be measured there, but its *shape* can. If the
events, or the verdicts, move away from what the classifier was evaluated on, the eval numbers stop
describing it. Every event the LLM classifier judges is profiled:
- **input:** event kind, tool, verified, text length, script (Latin, Arabic, …), history length;
- **output:** verdict, gate, rules, confidence, errors, latency.

A rolling window (last 500 events) is compared with a **reference profile** using the Population
Stability Index:
- below 0.1 is stable, 0.1–0.25 moderate, 0.25 and above major;
- rare categories are merged so small samples don't raise false alarms;
- a category the reference never saw (a new language, tool or event kind) is always reported.

When a feature's status changes, it logs one `drift detected` warning, and one `drift resolved`
when it recovers.

```bash
uv run pac monitor reference 20261010T114413Z_qwen3.5_test   # "normal" = the test-split run
uv run pac monitor compare 20261011T125927Z_qwen3.5_stress   # exits 4 on major drift (CI/cron)
curl localhost:8000/api/monitoring                           # live counts and drift, as JSON
curl localhost:8000/metrics                                  # the same for Prometheus
```

The committed reference ([`results/monitoring/reference.json`](results/monitoring/reference.json))
is the test-split run. Checked against it, the stress split shows what drift looks like: texts get
longer (major, including a new 400–1,000-character band), Arabic appears (reported as new), refunds
rise and the verdict mix shifts. The code is the `monitoring` context in
[`src/pac/monitoring/`](src/pac/monitoring/).

## Logs

Logs go to stderr and results to stdout, so `pac classify … | jq` stays clean. Every line carries
correlation ids, so one grep finds everything about one API request (`request=`, also returned as
the `X-Request-ID` header, or yours if you send one), eval run (`run=`), case (`case=`) or
conversation turn (`turn=`):

```text
16:42:17.385 WARNING guardrail.llm.caching [request=debug-123] no recording for this request model=qwen3.5
16:42:17.385 ERROR   guardrail.llm_classifier [request=debug-123] failed closed: held for a human kind=user_input reason="…"
16:42:17.385 INFO    interfaces.api [request=debug-123] POST /api/classify 200 ms=1
```

`PAC_LOG_LEVEL=DEBUG` (or `pac --log-level DEBUG …`) adds every classification and cache hit.
`PAC_LOG_FORMAT=json` writes one JSON object per line, for a log pipeline. The code is in
[`src/pac/telemetry.py`](src/pac/telemetry.py).

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
| [`prompts/`](prompts/) | Versioned classifier prompts (v1 kept for traceability; v2 is the default), and the simulated agent's instructions |
| [`data/build_cases.py`](data/build_cases.py) | The eval set, with each label's reasoning; generates `data/cases.jsonl` |
| [`data/build_hard_cases.py`](data/build_hard_cases.py) | The hard split (round 2), labelled before any model run; generates `data/hard_cases.jsonl` |
| [`src/pac/guardrail/`](src/pac/guardrail/) | Core domain: judging one event. Domain model, the LLM classifier use case, model adapters, regex baseline |
| [`src/pac/evaluation/`](src/pac/evaluation/) | Measuring a guardrail: labelled cases, metrics, eval use cases, run storage |
| [`src/pac/simulation/`](src/pac/simulation/) | The live demo's stand-in agent: Lark played by an LLM, and its simulated back office |
| [`src/pac/interfaces/`](src/pac/interfaces/), [`bootstrap.py`](src/pac/bootstrap.py) | CLI and API, and the composition root that wires them |
| [`cache/`](cache/) | Recorded model responses, which make every reported number reproducible offline |
| [`results/runs/`](results/runs/) | Committed eval runs |
| [`web/`](web/) | One-page React app |
| [`docs/WRITEUP.md`](docs/WRITEUP.md) | Reasoning, assumptions, results, limitations |
| [`docs/JOURNAL.md`](docs/JOURNAL.md) | How the classifier and its evaluation evolved, stage by stage, including reversals |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Where the guardrail sits (man in the middle), bounded contexts, layers, enforced dependency rules |
| [`docs/CORNER_CASES.md`](docs/CORNER_CASES.md) | What the policy does not decide, and what to do about it |
| [`docs/LABELING.md`](docs/LABELING.md) | Labelling conventions and who labelled |
| [`docs/POLICY_RATIONALE.md`](docs/POLICY_RATIONALE.md) | Why each rule exists and is worded as it is |
| [`docs/AI_OVERRIDES.md`](docs/AI_OVERRIDES.md) | Where AI-produced work was overridden |
