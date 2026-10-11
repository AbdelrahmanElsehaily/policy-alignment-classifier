# Data

```
data/
  eval/       the labelled eval sets: what every reported number is measured on
  builders/   the scripts that write them (the source of truth for every label)
  sources/    raw inputs and audits: recorded conversations, the blind relabel
  ui/         metadata the web app shows: curated examples, readable case names
```

## The eval sets

| Split | File | Cases | Labels (allow / violation / silent) | What it is | Built by | Last label change |
|---|---|---|---|---|---|---|
| dev | `eval/dev_test.jsonl` | 49 | 28 / 14 / 7 | Hand-written events for developing the prompt | `builders/build_cases.py` | `245e65a`, 10 Oct |
| test | `eval/dev_test.jsonl` | 114 | 53 / 41 / 20 | Hand-written events, never tuned on; the headline numbers | `builders/build_cases.py` | `245e65a`, 10 Oct |
| hard | `eval/hard.jsonl` | 31 | 11 / 8 / 12 | Realistic events the policy mostly doesn't decide, written after v1.0 | `builders/build_hard_cases.py` | `2527f48`, 11 Oct |
| stress | `eval/stress.jsonl` | 355 | 207 / 61 / 87 | 191 events from 16 recorded conversations, 62 from 5 scripted ones, and single events per corner case | `builders/build_stress_cases.py` | `27be069`, 11 Oct |

Each split's labels were committed before any model ran on it. Every later change to a label is
logged with its reason in [`docs/LABELING.md`](../docs/LABELING.md).

**Why four splits in three files.** The sets were built in rounds, and every committed run records its
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

- `sources/recordings/`: 16 conversations between a simulated customer and the agent, recorded with no
  guardrail running (`scripts/record_conversations.py`), plus `briefs.json`, the only part written by hand.
  Recordings are never edited; their events are labelled in `builders/build_stress_cases.py`.
- `sources/relabel/`: a blind relabel of 30 test cases by a second annotator, to check label agreement.
  See its README.

## UI

- `ui/demo_examples.json`: the curated examples in the app's Try it tab.
- `ui/case_titles.json`: a readable name for every case, shown in place of ids.
