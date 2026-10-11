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

## Reading it

- **No optimised prompt beats v2 on test.** Each one is 4 to 5 points lower. The test gap is about 5 cases out of 114, so this is suggestive, not conclusive.
- **On stress, the optimised prompts are ahead of v2 by 0 to 4 points**, but every interval overlaps v2's. The best, GEPA with MiniMax reflection, is 3.7 points ahead.
- **Validation gains don't carry over.** The Claude-reflection prompt gains 7.7 points on validation over v2, then loses 5.2 points on test. The optimisers chose their prompts on the same 65 cases they were judged on, so this is overfitting to the selection set.
- **Changing the reflection model made no difference.** GEPA with Claude reflection did no better than with MiniMax reflection, on either split.

## Cost of each optimisation

| Run | MiniMax calls (ledger) | OpenRouter calls | Failed |
|---|---|---|---|
| MIPROv2 | 1,217 | – | 5 |
| GEPA, MiniMax reflection | 543 | – | 0 |
| GEPA, Claude reflection | 569 | 7 (`anthropic/claude-opus-5.5`) | 2 |

Each eval run is about 114 (test) or 355 (stress) MiniMax calls, less cache hits.

## What to do next

Keep `prompts/classifier_v2.md` as the production prompt. The optimised prompts don't justify replacing it. Any future change should be selected on data other than the generated train split, and judged only on test and stress, once.
