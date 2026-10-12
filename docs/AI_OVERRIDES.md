# Where AI output was overridden

Claude (Claude Code) wrote all of this repo's code, data and documents, and the human directed the work. The
brief asks where AI output was overridden. This log records every place a first attempt was rejected or
changed, and why, whether the correction came from the human or from evidence.

## Overridden by the human

| What the AI produced | Override | Why |
|---|---|---|
| A first plan in which the classifier saw a 5-event history window by default, the AI wrote and labelled all cases, and an "ops agent" domain | Re-read the brief | The brief says the classifier reads **one** event, and the labelling is meant to be the candidate's own work. The plan was rewritten: single event by default, with history as an ablation. |
| A revised plan in which the human labels and writes the corner cases | "No, you will do everything" | The human chose to delegate everything. The consequence (an AI labeller) is disclosed in `LABELING.md` and the write-up rather than hidden. |
| Ollama as the default live backend | "I'm going to use MiniMax M3" | Added an OpenAI-compatible backend configured for MiniMax-M3. Ollama stayed for the committed runs. |
| — | "Commit as you go" | One commit per coherent step. |
| Results framed as dev / test / hard / stress / agents splits | "I don't like this" | The data is presented as what the prompt was tuned on (49 events) against what was held out (743), grouped by where it came from. |
| A claim that the guardrail works across agent models, from conversations recorded with one agent | "Make the claim true" | Conversations were recorded again with MiniMax-M3 as the agent (the agents split), then with that agent told to use its tools (agents-pressure). |
| Gold labels described as ground truth | "Who labelled these?" | Every label is disclosed as Claude's, with the 30/30 blind relabel described as consistency, not truth; human labels of the contested cases are the next step. |
| A recommendation to keep prompt v2 (v3's gain on test was within noise) | "Adopt v3" | v3 ships. The write-up gives the evidence for it (3–5 points on every natural-traffic set) and its caveats (one stress run each; v3's 95 training events excluded). |
| One model benchmarked, MiniMax-M3 | "Benchmark a frontier model" | Claude Haiku and Opus 5.5 on the same prompt and every held-out set (Sonnet 5.5 was run too and dropped: it sat between the two on every number), with cost per 1,000 events: the frontier model was no more accurate. |
| Synthetic data labelled while MiniMax's API allowed 2–3 calls at once (a cap from my own brief) | "Drop MiniMax if it's the bottleneck" | MiniMax-M3 calls went through OpenRouter at 32 at a time: discovery took 3.5 minutes instead of about 35. |
| The policy in the slides only | "Editing it should reflect on the classifier" | A Policy tab: with a model running, an edit applies to the classifier at once and can be reset. |

## Overridden by evidence

| First attempt | What showed it was wrong | Change |
|---|---|---|
| Relying on Ollama's JSON-schema `format` for structured output | With `qwen3.5` and thinking off, Ollama 0.20 ignored the schema and answered in prose. With thinking on it followed the schema but took about 97 seconds per call. | The schema is also stated in the prompt; output is parsed tolerantly and then validated strictly; anything unusable returns `policy_silent`. |
| Prompt v1 | Dev run: the model claimed facts the event didn't contain ("the order belongs to the verified customer") and flagged generic wording | Prompt v2: use only facts in the event, quote the text that breaks a rule, and return `policy_silent` with the missing fact named. |
| Retrying unparseable output with the same request | With temperature 0 and a cache, the same request returns the same bad answer | Retry with a repair instruction appended, which is a different request. |
| Bootstrap confidence interval computed in completion order | The regression test showed a replayed run with a different macro-F1 interval | Sort before resampling; recompute the committed metrics. |
| Recording latency only as wall time | A run replayed from the cache would report about 0 ms | The provider latency is stored with each cached response. |
| A policy that pictured the judge knowing the order | Labelling refund calls: the event carries none of the facts R5 depends on | Logged as finding #14 in `CORNER_CASES.md` (structural). Labelled `policy_silent` per the policy's own definition, and amendment A1 proposed. |
| A red-team prompt asking Claude to find events that "harm the company" and get past the guardrail | Claude Haiku refused 22 of 34 requests | Reworded as what the loop is: a coverage review for the policy's owner. Refusals fell to 1 in 40, and it found 5 policy gaps. |
| Telling red-team attempts apart by the event alone | 22 of 40 policy-loop attempts were "duplicates": different gaps made the same short tool call | Attempts are compared with their history and session; duplicates fell to 14. |
| Contrast pairs left out of the cross-split leakage check | A test found two dev events identical to two test events | Every kept event of a split is checked by the later splits. |
| A recorder that noticed a request to verify only in English | Arabic and French conversations never got verified (found by recording the agents-pressure split) | French and Arabic added; the 3 affected synthetic conversations recorded again. |
| An initial 60-second timeout for local runs | Two dev cases timed out with two requests running at once on one GPU | The timeout is configurable (`PAC_TIMEOUT_S`). Timeouts still fail closed and are counted as classifier errors. |
