# Data

```
data/
  eval/       the labelled events every reported number is measured on
  builders/   the scripts that write eval/ and train/: the source of truth for every label
  train/      events for optimising the prompt; never used to report a result
  sources/    raw material, never edited: recorded conversations and the blind relabel
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
| · hand-written, hard | 31 | 11 / 8 / 12 | Events the policy mostly doesn't decide, written after v1 |
| · recorded conversations, agent qwen3.5 | 191 | 135 / 35 / 21 | A simulated customer talking to the agent, unscripted |
| · recorded conversations, agent MiniMax-M3 | 123 | 104 / 4 / 15 | Eight of the same briefs, with another model as the agent |
| · scripted conversations | 62 | 45 / 6 / 11 | Five conversations, each built around one trap |
| · targeted probes | 102 | 27 / 20 / 55 | One family of events per corner case, plus history windows |

**One exception, for prompt v3 only.** v3 was optimised partly on 8 of the qwen3.5 conversations
(95 events, listed in `train/natural_conversations.json`). For v3 those are training data, so its
results on the stress set are reported on the other conversations only. Every earlier prompt saw
none of them.

## Eval files

| File | Events | Holds | Built by |
|---|---|---|---|
| `eval/dev_test.jsonl` | 163 | the 49 prompt-development events (`split: dev`) and the 114 hand-written held-out events (`split: test`) | `builders/build_cases.py` |
| `eval/stress.jsonl` | 386 | everything built after v1 with qwen3.5 as the agent: the 31 hard cases (tag `round:2`, from `builders/build_hard_cases.py`), then the recorded and scripted conversations and the targeted probes | `builders/build_stress_cases.py` |
| `eval/agents.jsonl` | 123 | the conversations recorded with MiniMax-M3 as the agent | `builders/build_agent_cases.py` |

Each line is one event: the event, its session envelope, any earlier events in the conversation, the
label, the rules it rests on, a confidence (`high`, `medium`, `contested`) and a written reason. Every
split's labels were committed before any model ran on it; every later change is logged with its reason
in [`docs/LABELING.md`](../docs/LABELING.md).

**Why dev and test share a file.** Every committed run records its dataset file's name and SHA-256,
and CI replays each run from its recorded answers. Splitting `dev_test.jsonl` would change its hash and
break those runs; the `split` field tells the two apart.

**Why the hard cases are inside the stress file.** They used to be a separate file, and were merged in
because both are realistic sets built after v1. The merge changed no event: every case is identical
apart from its split name and the `round:2` tag, and, after checking that, the records of the runs made
on the old files were updated to the new file's name and hash. Runs from before the merge still show as
"hard" or as the 355 round-3 events.

**Don't edit a file in `eval/` by hand.** Change its builder, then regenerate:

```bash
uv run python data/builders/build_stress_cases.py
```

Each builder also has `--check`, which CI runs to fail on a stale file.

## Train

- `train/train_cases.jsonl`: 257 generated events in their own world (customers C-21xx, orders
  LK-8xxxx), with checks that none duplicates an eval event. Built by `builders/build_train_cases.py`.
- `train/natural_conversations.json`: the 8 recorded conversations prompt v3 was also trained on.

## Sources

- `sources/recordings/briefs.json`: the short customer briefs, the only hand-written part of the
  conversations.
- `sources/recordings/qwen3.5/`: 16 conversations with qwen3.5 as the agent and the customer.
- `sources/recordings/minimax-m3/`: 8 of the same briefs with MiniMax-M3 as the agent and the customer.

  Both were recorded with no guardrail running (`scripts/record_conversations.py`), so each trace is
  what the agent does on its own. Recordings are never edited; their turns are labelled in the
  builders.
- `sources/relabel/`: a blind relabel of 30 test events by a second annotator, to check label
  agreement. See its README.

The web app's curated examples and readable case names live with the app, in `web/src/data/`.
