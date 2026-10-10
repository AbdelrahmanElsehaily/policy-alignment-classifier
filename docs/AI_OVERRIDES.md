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

## Overridden by evidence

| First attempt | What showed it was wrong | Change |
|---|---|---|
| Relying on Ollama's JSON-schema `format` for structured output | With `qwen3.5` and thinking off, Ollama 0.20 ignored the schema and answered in prose. With thinking on it followed the schema but took about 97 seconds per call. | The schema is also stated in the prompt; output is parsed tolerantly and then validated strictly; anything unusable returns `policy_silent`. |
| Prompt v1 | Dev run: the model claimed facts the event didn't contain ("the order belongs to the verified customer") and flagged generic wording | Prompt v2: use only facts in the event, quote the text that breaks a rule, and return `policy_silent` with the missing fact named. |
| Retrying unparseable output with the same request | With temperature 0 and a cache, the same request returns the same bad answer | Retry with a repair instruction appended, which is a different request. |
| Bootstrap confidence interval computed in completion order | The regression test showed a replayed run with a different macro-F1 interval | Sort before resampling; recompute the committed metrics. |
| Recording latency only as wall time | A run replayed from the cache would report about 0 ms | The provider latency is stored with each cached response. |
| A policy that pictured the judge knowing the order | Labelling refund calls: the event carries none of the facts R5 depends on | Logged as corner case #1 (structural). Labelled `policy_silent` per the policy's own definition, and amendment A1 proposed. |
| An initial 60-second timeout for local runs | Two dev cases timed out with two requests running at once on one GPU | The timeout is configurable (`PAC_TIMEOUT_S`). Timeouts still fail closed and are counted as classifier errors. |
