# Data

```
data/
  eval/       the labelled eval sets: what every reported number is measured on
  builders/   the scripts that write them (the source of truth for every label)
  sources/    raw inputs and audits: recorded conversations, the blind relabel
  ui/         metadata the web app shows: curated examples, readable case names
```

## What the eval measures on

672 labelled events. The prompt was written against 49 of them, so those never count in a result; the
other 623 are held out, grouped by where they came from. The natural conversations come from two agent
models, because the guardrail sits in front of whichever agent it protects.

| Events | Count | allow / violation / silent | What they are |
|---|---|---|---|
| Prompt written against | 49 | 28 / 14 / 7 | Hand-written; used to develop the prompt |
| **Held out** | **623** | 375 / 114 / 134 | |
| · hand-written | 114 | 53 / 41 / 20 | Realistic single events, never tuned on: the headline |
| · recorded conversations, agent qwen3.5 | 191 | 135 / 35 / 21 | A simulated customer talking to the agent, unscripted |
| · recorded conversations, agent MiniMax-M3 | 123 | 104 / 4 / 15 | Eight of the same briefs, with another model as the agent |
| · scripted conversations | 62 | 45 / 6 / 11 | Five conversations, each built around one trap |
| · targeted probes | 133 | 38 / 28 / 67 | One family of events per corner case, plus history windows |

## The files

The groups above live in four files, because the sets were built in rounds:

| Split | File | Cases | Labels (allow / violation / silent) | What it is | Built by | Last label change |
|---|---|---|---|---|---|---|
| dev | `eval/dev_test.jsonl` | 49 | 28 / 14 / 7 | Hand-written events for developing the prompt | `builders/build_cases.py` | `245e65a`, 10 Oct |
| test | `eval/dev_test.jsonl` | 114 | 53 / 41 / 20 | Hand-written events, never tuned on; the headline numbers | `builders/build_cases.py` | `245e65a`, 10 Oct |
| hard | `eval/hard.jsonl` | 31 | 11 / 8 / 12 | Realistic events the policy mostly doesn't decide, written after v1.0 | `builders/build_hard_cases.py` | `2527f48`, 11 Oct |
| stress | `eval/stress.jsonl` | 355 | 207 / 61 / 87 | 191 events from 16 recorded conversations, 62 from 5 scripted ones, and single events per corner case | `builders/build_stress_cases.py` | `27be069`, 11 Oct |
| agents | `eval/agents.jsonl` | 123 | 104 / 4 / 15 | 8 conversations recorded with MiniMax-M3 as the agent, from the same briefs | `builders/build_agent_cases.py` | `7d81479`, 12 Oct |

Each split's labels were committed before any model ran on it. Every later change to a label is
logged with its reason in [`docs/LABELING.md`](../docs/LABELING.md).

**Why the files are split this way.** The sets were built in rounds, and every committed run records its
dataset file's name and SHA-256, which CI replays. Adding a round as a new file left the earlier files,
and the runs measured on them, untouched. Splitting `dev_test.jsonl` now would change its hash and break
those runs, so dev and test stay together, told apart by each case's `split` field.

**Don't edit a file in `eval/` by hand.** Change its builder, then regenerate:

```bash
uv run python data/builders/build_cases.py
```

Each builder also has `--check`, which CI runs to fail on a stale file.

Runs made before this layout record the former file names (`cases.jsonl`, `hard_cases.jsonl`,
`stress_cases.jsonl`); `pac.bootstrap.dataset_file` maps them to the files above.

## Sources

- `sources/recordings/`: 16 conversations between a simulated customer and the agent (qwen3.5),
  recorded with no guardrail running (`scripts/record_conversations.py`), plus `briefs.json`, the only
  part written by hand. `sources/recordings/minimax-m3/` holds 8 of the same briefs with MiniMax-M3 as
  the agent.
  Recordings are never edited; their events are labelled in `builders/build_stress_cases.py`.
- `sources/relabel/`: a blind relabel of 30 test cases by a second annotator, to check label agreement.
  See its README.

## UI

- `ui/demo_examples.json`: the curated examples in the app's Try it tab.
- `ui/case_titles.json`: a readable name for every case, shown in place of ids.
