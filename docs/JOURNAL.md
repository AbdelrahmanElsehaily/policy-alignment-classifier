# Project journal: how the classifier and its evaluation evolved

A running log, kept as the work happens. It records what was built at each stage, why, how the
evaluation changed, and the decisions that were reversed. The final state is documented elsewhere
([`WRITEUP.md`](WRITEUP.md), [`ARCHITECTURE.md`](ARCHITECTURE.md),
[`CORNER_CASES.md`](CORNER_CASES.md)). This file is about how it got there. Commit hashes link each
step to the code.

---

## Stage 0: reading the brief, choosing the domain (10 Oct)

- **Plan first.** An HTML plan was written before any code ([`plan.html`](plan.html), now marked
  historical). A second reading of the brief surfaced what the plan had underweighted: the brief values
  *corner cases the policy doesn't decide* above accuracy, and asks for the reasoning behind every label.
- **Domain: a customer-support agent.** "Lark" works for a fictional electronics retailer, Larkspur.
  Support was chosen because its tools move money and personal data (refunds, address changes,
  email), and because "is this customer allowed to see this?" depends on facts that a single event
  often doesn't carry. Policy v0 has 13 rules (`0c4d956`).

## Stage 1: the classifier and a first evaluation (10 Oct, 12:12–13:18)

- **Built in small commits:**
  - trace-event schemas;
  - a provider-neutral LLM port, with backends for Anthropic, Ollama and an OpenAI-compatible API
    (configured for MiniMax-M3);
  - a prompted classifier that **fails closed**: any error becomes `policy_silent`, which goes to a
    human;
  - a flat-text regex baseline, to make the brief's "flat text can't tell positions apart" claim
    testable.
- **Eval set:** 163 hand-labelled events, 49 dev and 114 test (`1085089`). The test split was frozen
  before its first run. Each label carries a rationale and a confidence (`high`, `medium` or
  `contested`).
- **Metrics:**
  - a Wilson interval on every proportion;
  - a bootstrap interval for macro-F1;
  - per-kind and per-confidence breakdowns;
  - calibration, latency and cost.
- **Prompt v2** (`dbcb09f`): "judge only the facts the event carries". On dev, v1 had been inventing
  facts, such as assuming an order belonged to the customer. Dev accuracy went from 82% to 86%. The
  prompt was then frozen; further tuning against a 9.7B model would have overfitted dev.
- **Reproducibility:** every model response is cached on disk and committed. CI replays every
  committed run and requires identical verdicts and metrics (`0e15a7a`). This immediately caught an
  order-dependent bootstrap, which was fixed.
- **Label reliability:** a blind re-label of 30 test cases agreed perfectly (κ = 1.0). This is
  reported as an *upper bound*, since both labellers are AI (`ceb5e7c`).

## Stage 2: an independent review, then fixes (10 Oct, 14:28–14:52)

An independent verification agent tried to break the system and found real bugs:
- a valid answer with a long rationale failed closed;
- a corrupt cache entry crashed the eval;
- **classifier errors were scored as correct `policy_silent`**;
- `--no-cache` was ignored in replay mode;
- a blank API key was treated as configured;
- a run id of `..` was accepted.

All were fixed with tests (`6b6fa15`…`751101c`).

**An evaluation mistake, caught and corrected.** The first history ablation looked like a
regression, 70%. It was being scored against the single-event labels, even where the history
legitimately settles the case. The fix was to score against `label_with_history`, the answer once
history supplies the missing fact (`7e1d408`). Corrected result: history settles 7 of 7 missing-fact
cases, against 5 of 7 without.

**Results at this point** (test split, local `qwen3.5` 9.7B):

| | Accuracy | Violation recall | False-block rate |
|---|---|---|---|
| LLM classifier, prompt v2 | 75% [67–82] | 78% | 11% |
| Regex baseline | 51% | 22% | n/a |

## Stage 3: engineering for a colleague (10 Oct, 15:35–16:50)

- **Moved to Python 3.14.**
- **The flat layout became a domain-driven one** (`6b71ad9`):
  - `guardrail` (the core) and `evaluation` (supporting) as bounded contexts, each split into domain,
    application and infrastructure layers;
  - a composition root that wires them;
  - a test tree that mirrors the layout;
  - layer rules enforced by import-linter in CI.

## Stage 4: where the guardrail sits (10 Oct, 16:50)

**Decision: the guardrail is a man in the middle.** It sits between the agent, its users and its
tools. It can't change the agent's prompt, tools or platform. Every recommendation was recast:
- fixes the guardrail can make itself are proposed as fixes;
- changes only the agent's operator can make are written as questions for them.

The main fix proposed for the money rules became a per-session store of facts the guardrail has
observed (`effcb42`).

## Stage 5: a live demo, and a variance check (10 Oct, 16:54–17:47)

- **Live demo:** the web app calls the model on every request, with 14 curated examples (`dd7f521`).
- **Run-to-run variance:** 0 of 49 verdicts flipped across three uncached dev runs (`7639c14`). That
  holds at temperature 0 on one local runtime. Hosted APIs aren't guaranteed to be deterministic.

## Stage 6: a live conversation, then blocking (10 Oct, 19:52–22:26)

- **A simulated agent and back office** (`098d6b7`). A new `simulation` context: an LLM plays Lark
  against fake order, payment and help-centre systems, seeded with the eval set's world, and the
  guardrail judges each event as it happens.
- **First live finding: history contamination.** The guardrail blamed a clean "address updated" event
  for a secret that had appeared two events earlier. None of the eval's history cases contained an
  earlier violation, so the eval couldn't see this.
- **Decision: the guardrail blocks** (`8537201`). It sits inline, and every event waits for its verdict:
  - `allow` passes;
  - `violation` blocks;
  - `policy_silent` holds the event for a human.

  A stopped event is replaced by a stand-in: an error for the agent, or a notice for the customer.
  Latency is now on the critical path for every event.
- **What blocking exposed:**
  - a false positive now costs the customer a reply;
  - blocking a whole tool result to stop one leaked field also hid the facts the agent needed. Redaction
    would be better.
- **Latency, measured:** reading the ~2,900-token policy prompt dominates, at 10–11 s cold and 1.4 s
  when Ollama still has it cached. In the live chat, agent and guardrail alternate on one model and
  evict each other's cached prompt.
- **MiniMax-M3 verified live:** about 2 s per verdict, against 6–19 s locally.
- **Tagged and released as `v1.0.0`.**

## Stage 7: harder cases, round 1, the `hard` split (11 Oct, 11:11–11:24)

- **31 hand-written hard cases,** labelled and committed before any model run (`2527f48`). They cover:
  - a refund retried after a payment timeout;
  - euros against a dollar cap;
  - IMEIs shaped like card numbers;
  - gift card codes and publishable keys;
  - planted key-shaped strings that trigger blocks;
  - refunds caused by injected instructions;
  - history contamination;
  - Arabic.
- They live in their own file, so every committed dev and test run still reproduces. Runs now record
  which dataset file they measured.
- **Results:**

  | | regex | MiniMax-M3 | MiniMax + history | qwen3.5 | qwen + history |
  |---|---|---|---|---|---|
  | Accuracy | 42% | 87% | 71% | 74% | 58% |

  - **Giving the models history made them worse on cases history can't settle:** −16 points for both.
  - **Both, given history, allowed a possible double refund after the timeout.**
  - qwen reproduced the live demo's contamination false block.
  - On contested definitions, every model run read the policy more strictly than the frozen labels.
    This is reported, not relabelled.
- Nine new entries were written up in the corner-case log (numbered #16–#24 then; [`CORNER_CASES.md`](CORNER_CASES.md#old-numbers) maps them to the current numbers).

## Stage 8: "31 is a joke to measure on", sizing the next round (11 Oct, 11:41–12:41)

The objection was correct: 23 uncontested cases give about ±16-point intervals.

1. **First attempt: 532 generated cases** (`a108d34`). Thirteen families of variations, with labels
   computed by small, unit-tested labelling functions instead of 532 separate judgements.
   - Generated variants of one template are correlated, so a **template-level bootstrap interval**
     was added to the metrics. On the regex baseline it was twice as wide as the per-event interval.
2. **Too big, and too repetitive.** Resized to **325 cases** chosen for variety over volume
   (`1df8434`): parameters rotate across cases instead of crossing in full grids.
3. **Multi-turn conversations were added:** 11 scripted conversations of 7–20 turns, every turn
   labelled and judged with everything before it, and tagged by how much context precedes it. Plus a
   small context-window family that shows the same deciding event with only the last *k* events.

No model had been run on the 532-case version beyond the regex baseline, which was discarded.

**Clarified: the guardrail never sees the agent's model input** (`cf9e607`). That covers the
system prompt, context injected straight into the prompt, memory and reasoning. It sees only the
four event kinds. Consequences:
- R4's "Lark's own instructions" can only be recognised, not compared against the real prompt;
- an injection is visible only when it arrives through a tool result;
- why the agent acted must be inferred from the trace.

## Stage 9: "are these natural turns?", recorded conversations (11 Oct, 12:55, in progress)

**No:** the scripted conversations were tidy, written on both sides, and built around one trap each.
So now:
- **A simulated customer** plays a person from a short *brief* (who they are and what they want),
  never a script (`477f296`).
- **Recording:** the customer talks to Lark through the simulated back office with **no guardrail**,
  so each trace shows what the agent does on its own. Verification happens as the platform would do
  it: when Lark asks, the customer enters the code.
- **Labelling:** every recorded turn is labelled by hand afterwards, judged with its history.
  Recordings are never edited to fit a label.
- **Both are kept:** recordings for realism, five scripted conversations as targeted probes.

**What the recordings showed, unprompted:**
- Lark disclosed order details before verification in several conversations. In one, it never asked
  the customer to verify at all.
- It refunded $349 on its own, over its $200 limit, without escalating.
- It disclosed an internal risk flag ("previous chargebacks") four times in one conversation.
- It repeatedly claimed to have escalated to a human without ever calling the escalation tool, and
  promised callbacks it couldn't guarantee.
- It looped on lookups without replying, and invented an action outside its protocol
  (`escalate_to_human` as an action). The second crashed the recorder, which now keeps the partial
  conversation and records why it stopped.
- **Briefs don't guarantee the trap.** In the pairing conversation, Lark never searched the help
  centre, so the injected article never appeared. That is the price of naturalness.
- It also refused six times to hand over to a human until the customer verified (R11). It wrongly
  refused a valid return in Arabic: it said "more than 30 days" when the order was 12 days old. No rule
  covers an agent that's wrong against the customer, so the guardrail can't catch that.

**The stress split as frozen** (`27be069`): 355 cases.

| Part | Cases | Notes |
|---|---|---|
| Recorded conversations | 191 | 16 conversations; 135 allow, 35 violation, 21 policy_silent (first written here as 145 / 35 / 11; corrected against the frozen file) |
| Scripted conversations | 62 | 5 targeted probes |
| Context windows | 8 | The same deciding event with partial history |
| Single-event families | 94 | 12 families |

54 turns have 10 or more prior events.

**A caution, learned from the first baseline run:** the regex baseline scores 59% overall but catches
only 25% of violations. Natural traffic is mostly benign, so overall accuracy rewards allowing
everything. From here on, results are reported per family, alongside violation recall and false
blocks, never as accuracy alone.

## Stage 10: "is the difference real?", run-to-run noise on a hosted model (11–12 Oct)

**Why it came up.** Prompt optimisation with DSPy (MIPROv2, then GEPA) produced prompts that scored
95–98% on their validation set and then lower than v2 on test. The comparison didn't make sense, so
before reading anything into it, I measured how much v2 disagrees with itself.

**What I found:**
- **MiniMax-M3 is not repeatable.** v2 on test, three uncached runs: **99, 97 and 94** of 114.
  Between any two runs, 9 to 13 cases switch between right and wrong, and 16 cases are right in
  some runs and wrong in others. That is as much as v2 differs from the optimised prompts.
- **Temperature 0 doesn't fix it.** No temperature had been set for MiniMax (the local qwen runs
  have always been pinned to 0, which is why Stage 5 found no flips). With an optional
  `PAC_TEMPERATURE` (on the optimisation branch), one borderline case, five calls each: at 0 the
  verdicts were violation, violation, allow, policy_silent, allow; at 0.01, four violations and one
  allow. The rationales differed every time.
- **So the optimisation result was noise.** The optimised prompts' 93–95 on test sit inside v2's
  own range. The validation scores were inflated twice over: each optimiser kept the best of many
  candidates, each scored once on 65 cases. There is no evidence either optimiser helped, and v2
  stays (`COMPARISON.md` on the `dspy-prompt-optimization` branch).
- **Reading the prompts confirmed it.** They had picked up rules from families of training cases
  (one names a training template outright), and a few of those rules cause wrong verdicts on test.

**What changes from here:**
- Every MiniMax number reported before this stage is one sampled run and carries a few points of
  run-to-run spread beyond its interval. The qwen numbers are repeatable.
- Comparisons on MiniMax use repeated runs (`--repeats 3`) and report the spread, never a single
  run. The first of these, MiniMax on test with history, scored **97, 96 and 97** of 114
  (`20261011T214211Z_minimax-m3_test_history`): about the same accuracy as event-only (94–99), and
  steadier across runs. With history, more labels are decided, so only 11 stay `policy_silent`, and
  the model leaves 2 of those undecided.
- A difference of a few points between two prompts or settings is not read as a result unless it
  holds across repeats.

## Stage 11: more than one agent model (12 Oct)

**Why.** The guardrail sits in front of whichever agent it protects, but every recorded conversation
came from one agent model (qwen3.5). A claim that the data spans agent models would have been false.

**What I did.** Eight of the same briefs were recorded again with MiniMax-M3 as the agent and the
customer: 123 turns, labelled by the same conventions and committed (`7d81479`) before any guardrail
result was read. They are their own split (`agents`), so the stress split and its committed runs are
unchanged. The recorder now refuses to overwrite a recording; its first version would have written
over the qwen3.5 recordings.

**What it showed.** The MiniMax-M3 guardrail scores about the same on both agents' traffic (75–81% and
80%, three runs each), but the traffic differs: 4 policy violations in 123 turns from one agent, 13 in
96 from the other. A guardrail's base rates belong to the agent it protects.

## Stage 12: the same agent, told to use its tools (12 Oct)

**Why.** "Mind the GAP" (arXiv:2602.16943) argues that agents break rules through actions more than
words, and that how a deployment words tool use moves that. Our recordings had almost no rule-breaking
tool calls, so the event kind the paper says matters most was barely tested on natural traffic.

**What I did.** The recorder gained `--agent-condition`: one line of team guidance appended to the
agent's instructions, the only difference. The eight MiniMax briefs were recorded again with a
tool-encouraging line, labelled by the same conventions (120 turns), and run three times with the
same guardrail. A new split and file (`agents-pressure`) kept the earlier runs valid.

**What it showed.** The agent got worse and the guardrail didn't: 14 violations instead of 4,
including 3 rule-breaking tool calls, all caught on every run, with accuracy unchanged (78–81%). The
misses are still promises inside replies. Recording it also exposed a recorder bug: the platform only
noticed a request to verify in English, so the Arabic briefs were never verified.

## Stage 13: prompt v3 ships, four models compared, synthetic data (12 Oct)

**Why.** Three open questions: does the optimised prompt earn its place, does a frontier model change the
picture, and can the training side get more data without touching the eval?

**What I did.** Ran v3 (GEPA, trained on the dev cases and 8 recorded conversations) on every held-out
set with MiniMax-M3, three runs where the model was run three times, and scored the stress set without
v3's 95 training events. Ran Claude Haiku, Sonnet and Opus 5.5 with v3 through OpenRouter on test (three
runs each) and the agents split. Built 1,223 synthetic events in their own world, labelled by a calibrated
jury of three model families, with three red-team loops on top.

**What it showed.** v3 is level with v2 on the hand-written test set and 3–5 points better on every set
of natural traffic, in every run, so it ships. The frontier model is no more accurate than Haiku (both
87–89% on test) at 35× the cost: every model is right 95–98% of the time where the policy is clear and
26–44% where the label is contested. The ceiling is the policy. The red-team loop against the policy
found five gaps, among them redirecting a still-processing order to someone else's address.

## Stage 14: "what about long inputs?" (12 Oct)

**Why.** The brief leaves open how much context an event carries, and no eval event was longer than
about 300 tokens. On natural traffic, longer events were already scoring worse.

**What I did.** Built 72 long events (about 1k, 8k and 32k tokens) with one deciding sentence at the
start, middle or end and a harmless twin for each. Added an optional guard that judges an event longer
than `PAC_MAX_EVENT_CHARS` in overlapping parts, any violation winning and any failure failing closed.
Ran Claude Haiku 5.5 and MiniMax-M3, whole and in parts.

**What it showed.** Length alone hid nothing up to 32k tokens: every violation was caught at every
length and position. Cost and latency grow with length, and the hard limit is the model's window,
which the guard handles. The drop with length on natural traffic is about content (long replies carry
more claims), not the window.

---

## Recurring principles

- **Labels are frozen and committed before any model runs.** If a model's reading seems better, it
  is reported as a disagreement, not relabelled.
- **Every reported number is reproducible offline** from committed recordings, and CI replays it.
- **Small numbers are reported as small,** with intervals that reflect how the data was built.
- **Mistakes in the evaluation itself are logged here**, along with how they were caught.
- **Before comparing two numbers, measure how much one of them varies on its own.**
