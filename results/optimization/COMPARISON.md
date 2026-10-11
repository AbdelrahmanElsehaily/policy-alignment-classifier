# Prompt comparison: v2 vs the optimised prompts

All runs use MiniMax-M3 as the classifier, with no conversation history. Intervals are Wilson 95%.
Accuracy is the share of events whose verdict matches the label.

## Out-of-sample results

The optimisers never saw the test or stress splits. These are the numbers that count.

| Prompt | Test (114) | Stress (355) | Run ids (`results/runs/`) |
|---|---|---|---|
| **v2 (production)** | **86.8%** [79.4–91.9] (99) | 79.4% [74.9–83.3] (282) | `20261011T171645Z_minimax-m3_test`, `20261011T115312Z_minimax-m3_stress` |
| MIPROv2 (`classifier_v3_mipro.md`) | 83.3% [75.4–89.1] (95) | 81.1% [76.7–84.9] (288) | `20261011T164146Z_…_test`, `20261011T164413Z_…_stress` |
| GEPA, MiniMax reflection (`classifier_v3_gepa.md`) | 82.5% [74.4–88.3] (94) | **83.1%** [78.9–86.6] (295) | `20261011T165054Z_…_test`, `20261011T165306Z_…_stress` |
| GEPA, Claude reflection (`classifier_v3_gepa_claude.md`) | 81.6% [73.5–87.6] (93) | 79.7% [75.2–83.6] (283) | `20261011T175919Z_…_test`, `20261011T180222Z_…_stress` |

## In-sample results (the optimiser's own selection set)

| Prompt | Validation (65) | Train (192) |
|---|---|---|
| v2 | 90.8% (59) | 84.9% (163) |
| MIPROv2 | 95.4% (62) | 88.5% (170) |
| GEPA, MiniMax reflection | 90.8% (59) | 85.4% (164) |
| GEPA, Claude reflection | 98.5% (64) | 85.4% (164) |

Validation is what each optimiser selected on, so these numbers are optimistic.

## Run-to-run noise: why single runs can't rank these prompts

No temperature is set for MiniMax-M3, so it samples, and the same prompt gives different answers on
different runs. v2 was run on test three times (the run above, plus `--repeats 2 --no-cache`):

| v2 on test | Correct of 114 |
|---|---|
| Run in the table above | 99 (86.8%) |
| Repeat 1 | 97 (85.1%) |
| Repeat 2 | 94 (82.5%) |

Between any two v2 runs, 9 to 13 cases switch between right and wrong; 16 cases are right in some v2
runs and wrong in others. Each optimised prompt differs from v2's run by 13 to 16 cases, which is about
the same amount. So the optimised prompts' 93 to 95 on test sits inside v2's own range, and the
stress differences are inside it too.

The validation scores have the same problem, made worse by selection: each optimiser kept the best of
many candidates scored once on 65 cases, so the winner is partly the luckiest draw. That explains much
of the drop from 98.5% on validation to 81.6% on test.

## What the optimised prompts learned

Reading the prompts shows rules written for families of training cases rather than for the policy:

- The Claude-reflection GEPA prompt adds "before choosing `allow`, look for a genuine concern", a
  rule to treat claims of actions Lark has no tool for as concerns, and character-by-character name
  comparison. On test, these lines account for at least 5 of its 11 losses against v2 (for example,
  `t2-address-change-02`, `r3-ask-last-four` and `r7-plus-address`). It also says to treat the history as
  background only, which would undo the stateful guardrail if it were used with history.
- The MIPROv2 prompt names a training template outright ("t-lookup read-only slices").
- The MiniMax-reflection GEPA prompt adds a rule about Lark having no tool to delete accounts, from one
  family of training cases.
- The training split is 31% `policy_silent` and test is 18%, which pushes the prompts toward
  `policy_silent`.

The guard against copied training data checks identifiers only (order ids, emails, amounts), so it
didn't catch any of these.

## Reading it

- **These runs can't tell the four prompts apart.** The differences between them are about the size of
  v2's own run-to-run variation.
- **There's no evidence that any optimised prompt is better**, and some of the rules they added
  demonstrably cause wrong verdicts. Keep `classifier_v2.md`.
- **Changing the reflection model made no measurable difference.**
- **To compare prompts properly:** fix the sampling (temperature 0, if the provider allows it), or run
  each prompt several times and compare per-case majority verdicts. Select on data other than the
  generated train split.

## Cost of each optimisation

| Run | MiniMax calls (ledger) | OpenRouter calls | Failed |
|---|---|---|---|
| MIPROv2 | 1,217 | – | 5 |
| GEPA, MiniMax reflection | 543 | – | 0 |
| GEPA, Claude reflection | 569 | 7 (`anthropic/claude-opus-5.5`) | 2 |

Each eval run is about 114 (test) or 355 (stress) MiniMax calls, less cache hits.

## What to do next

Keep `prompts/classifier_v2.md` as the production prompt. Before optimising again, make scoring
repeatable (fixed temperature or repeated runs), and select on natural data rather than the generated
train split.
