# Synthetic data and red-team loops

Labelled synthetic traffic for the Lark guardrail, built around what this repo already learned,
and three adversarial loops: one against the classifier, one against the policy, one against
enforcement. Everything here is **for training, robustness checks and finding corner cases**.
None of it is part of the reported eval, and none of it was tuned on the eval test, stress or
agents labels.

## In numbers

| Split | Events | allow / violation / silent | From conversations | Contrast pairs, hard negatives, targeted |
|---|---|---|---|---|
| train | 628 | 382 / 142 / 104 | 483 | 145 |
| dev | 273 | 169 / 52 / 52 | 177 | 96 |
| test | 322 | 197 / 73 / 52 | 228 | 94 |

- **Conversations:** 65 simulated support chats with four agent models (MiniMax-M3, DeepSeek V4 Flash,
  Mistral Small 3.2, Llama 4 Scout), 17 of them with the eval's tool-encouraging line in the agent's
  instructions. Each split has its own fictional customers and orders, and the test split holds out its
  agent model.
- **Labels:** a jury of three model families (MiniMax-M3, Claude Haiku 5.5, GPT-6 Luna) plus rule-based
  labelling functions. Calibrated first against the dev and train gold labels: **94.8% agreement,
  κ 0.92** on 306 cases ([`reports/calibration.md`](reports/calibration.md)). The eval's test, stress
  and agents labels were never used.
- **Leakage:** every event is checked against every eval set and the other splits; 253 near-duplicates
  were dropped ([`reports/`](reports/)).
- **The classifier against the jury:** 81.8% agreement on the first run; 18 jury violations passed
  twice. Those, and every split jury vote, are in `review_queue.jsonl` for a human.
- **Red-team loops:** against the classifier, 1 success in 40 attempts; against the policy, 5 gaps in
  40 (`policy_gaps.jsonl`); against enforcement, 40 guarded conversations (`reports/`).

Two versions per split, in the eval's own format so `scripts/optimize_prompt.py --extra-train` reads
them: `base_rate` (the conversations as they happened) and `enriched` (balanced across rule × event
kind × verdict, rare high-stakes cells oversampled).


## Files

| Path | What it is |
|---|---|
| `events/{train,dev,test}.jsonl` | The labelled events: request, label, rules, confidence, jury votes, source, agent model |
| `versions/` | The two versions per split, in the eval's `LabeledCase` format, and their manifest |
| `briefs.json` | The 65 conversation briefs (customer goal, persona, language, agent model and condition) |
| `review_queue.jsonl` | Events for a human: split jury votes, and jury–classifier disagreements that held twice |
| `adversarial/`, `policy_gaps/`, `policy_gaps.jsonl`, `audit_sample.jsonl` | The red-team loops' kept results and a 10% audit sample |
| `reports/` | Calibration, coverage, quality and leakage, costs |

Not committed, to keep the repository small: the raw recordings, each juror's per-rule ballots, the
calibration logs, every red-team attempt and the 44 MB response cache. The commands below rebuild them
(with API keys; about $2.30 on OpenRouter for the whole pipeline).

## Run it

```bash
uv run python scripts/synthesize.py budget                     # spend so far (needs OPENROUTER_API_KEY)
uv run python scripts/synthesize.py calibrate --iteration 3    # guideline 5
uv run python scripts/synthesize.py seeds                      # guideline 1
uv run python scripts/synthesize.py record                     # guideline 2 (never overwrites)
uv run python scripts/synthesize.py singles [--fill 45]        # guidelines 3 and 4
uv run python scripts/synthesize.py label conversations        # guideline 5
uv run python scripts/synthesize.py label singles
uv run python scripts/synthesize.py assemble                   # guidelines 3, 6, 7
uv run python scripts/synthesize.py discover                   # guideline 8
uv run python scripts/synthesize.py redteam classifier --iterations 40   # loop 1 (resumes)
uv run python scripts/synthesize.py redteam policy --iterations 40       # loop 2 (resumes)
uv run python scripts/synthesize.py redteam enforcement --iterations 12  # loop 3
uv run python scripts/synthesize.py report
```

The pure logic is the `synthesis` bounded context (`src/pac/synthesis/`: domain, application,
infrastructure; unit-tested in `tests/unit/synthesis/`). Like `evaluation`, `simulation` and
`monitoring`, it sees only the guardrail's domain model and its LLM port; two import-linter
contracts enforce that it never imports the classifier, its prompting or its adapters, and
never another supporting context. `scripts/synthesize.py` composes it with `simulation`
(recording), `monitoring` (drift) and the classifier. `tests/artifacts/test_synthetic_data.py`
checks the committed files (leakage, labels, formats).
