# Prompts

| File | What it is |
|---|---|
| `classifier_v3.md` | **The shipped classifier prompt.** v2's instructions, rewritten by GEPA (DSPy) with v2's dev cases and 8 recorded conversations as training data. |
| `classifier_v2.md` | The hand-written prompt every v1.x result was measured with. |
| `classifier_v1.md` | The first prompt; superseded after the dev runs (see `docs/JOURNAL.md`). |
| `lark_agent.md` | The simulated support agent's own instructions. The guardrail never sees them, and the agent never sees the classifier's prompt. |
| `experiments/` | Optimiser candidates that were not adopted (MIPROv2, and GEPA trained on the generated train split); compared in `results/optimization/COMPARISON.md`. |

Each run records the prompt's version and a hash of its text (`classifier_v3@55286419`), so a result can always be traced to the exact prompt.
