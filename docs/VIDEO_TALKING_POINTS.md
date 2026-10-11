# Video talking points

**These are cues to speak from, not a script.** The brief says "talk to us, don't read to us — no
AI-written script". Each segment lists the screen, the facts and numbers to have in mind, and the one
point to land. Say it in your own words; stumbles are fine.

Deck: <https://claude.ai/artifact/4xnPyJZQxHv2z5WbC4Uf1M> (slide numbers below refer to it).
Target: **4:35**, under the 5:00 limit, with room for one stumble. About 60% of it is findings and
limits (segments 3 and 7–11).

## Before recording

- Start the app with a real backend, because the live conversation needs one:
  `PAC_BACKEND=minimax PAC_MODEL=MiniMax-M3 MINIMAX_API_KEY=… uv run pac serve --live`
  (about 2 s per verdict; the local qwen3.5 takes 20–40 s per event in the chat, too slow for video).
- Do one dry run of both demo clicks. MiniMax samples, so the live run can differ from the dry run.
  If it does, say so: that is the run-to-run point from slide 11.
- Camera on. Deck in one window, app in another.

## Time budget

| # | Segment | Screen | Time | Ends at |
|---|---|---|---|---|
| 1 | What it is, where it sits | Slides 1 → 3 | 0:20 | 0:20 |
| 2 | Demo: same text, four positions | App · Try it | 0:25 | 0:45 |
| 3 | Demo: a corner case happening live | App · Live conversation | 0:35 | 1:20 |
| 4 | Choices and assumptions | Slide 4 | 0:15 | 1:35 |
| 5 | How the eval was built | Slide 5 | 0:15 | 1:50 |
| 6 | The number, and the cut that matters | Slide 6 | 0:15 | 2:05 |
| 7 | Corner case 1: verification breaks R3 | Slide 7 (or Findings tab) | 0:30 | 2:35 |
| 8 | Corner case 2: help centre vs policy | Slide 8 | 0:25 | 3:00 |
| 9 | Corner case 3: refund retried after timeout | Slide 9 | 0:20 | 3:20 |
| 10 | Where it breaks: the classifier | Slide 10 | 0:25 | 3:45 |
| 11 | Where it breaks: the measurement | Slide 11 | 0:25 | 4:10 |
| 12 | Cost on the request path | Slide 12 | 0:15 | 4:25 |
| 13 | Close | Slide 13 | 0:10 | 4:35 |

Findings and limits (3, 7–11, plus the right half of 6): about 2:50 of 4:35. Slide 2 is the fallback
for segment 2; slide 14 (appendix) is not for the video.

## Segments

### 1 · What it is, where it sits (0:20) — slides 1, 3

- One trace event in, `allow` / `violation` / `policy_silent` out. A customer-support agent for a fictional
  electronics retailer.
- Inline man in the middle: every event waits. Pass, block, hold for a human.
- It only sees the four event kinds. It can't change the agent, so every fix is something it can do itself.

### 2 · Demo: same text, four positions (0:25) — App, **Try it**

Click path:
1. Tab **Try it**.
2. **Start from** dropdown → group *Same text, four positions* → "The checkout key is sk_live_…".
3. Click **Try this text in all four positions**.
4. Point at the table *The same text in each position*: the `user_input` row against the
   `tool_response` row, then the LLM column against the regex column.

Cues:
- The policy (R4): a key the customer pastes is fine; the same key in a tool result is a leak.
- The regex sees flat text. Position pairs on test: LLM 10/10, regex 7/10.

### 3 · Demo: a corner case happening live (0:35) — App, **Live conversation**

Run this once **before you start recording**: one turn takes 30–60 s of model calls, and the agent
may not retry. The tab keeps the transcript, so on camera you switch to it and walk through the finished
conversation; only show it if the retry happened.

Click path (before recording):
1. Tab **Live conversation**.
2. Featured chip **Refund, then a gateway timeout** (it verifies the session as Ana), then **Send**.
3. Let it run. Point at the gate label on each event (passed / blocked / held) and the regex column,
   which never blocks.
4. Point at the `tool_response` with the gateway timeout, then at what happens to any second
   `issue_refund`.

Cues:
- The refund went through, but the gateway said "status unknown". Does the agent retry, and does the
  guardrail let it?
- That's corner case 3; come back to it on slide 9.
- If the agent didn't retry: use the chip **$349 refund, blocked, then split?** instead (told "no", the
  agent splits the refund into $200 and $149).

### 4 · Choices and assumptions (0:15) — slide 4

- Support domain: the typist is an unverified outsider, the reverse of the brief's ops example.
- One event plus a session envelope. That's an assumption about what a man in the middle can see.
- Labels read the policy literally: missing fact → `policy_silent`. Lower headline, visible problem.
- Override: the AI's first plan gave the classifier 5 events of history by default. I re-read the brief
  and cut it to one; history became an ablation.

### 5 · How the eval was built (0:15) — slide 5

- Test, 114 events, frozen before the first run. Hard split labels committed before any model saw them.
- Blind relabel: 30/30, κ = 1.0. An upper bound, because both annotators are Claude.
- One label changed after the test run, and it lowered the score. One disputed, left alone (that's case 1).

### 6 · The number, and the cut that matters (0:15) — slide 6

- 75% for local qwen, 82–87% for MiniMax over three runs, 51% for regex. Don't dwell on it.
- Land the bars: 86% where the label is high-confidence, 31% where it's contested.
- As good as the policy is clear.

### 7 · Corner case 1: the verification flow breaks R3 (0:30) — slide 7

Optional instead of the slide: tab **Findings** → corner case 1 → click its case chip ("Open it in
Try it") to show the recorded verdict.

- R3: never ask for one-time codes. Verification *is* asking for a one-time code.
- I labelled "enter it here" `allow`. The model said R3 violation. On the letter it's right.
- I left the frozen label alone and logged the conflict. Tuning the test set to the model would be worse.
- Ask: where does the customer type the code? Ship: pointing to the secure field passes; asking in chat
  is blocked.

### 8 · Corner case 2: help centre vs policy (0:25) — slide 8

- The KB says agents may refund up to $500. The policy says $200.
- R12 judges by channel, so this is "injection", but staff probably wrote it.
- Flag it: you call the company's docs an attack. Allow it: the KB overrides the policy.
- Ship: policy is the only authority; hold with a "policy/KB conflict" reason so the article gets fixed.

### 9 · Corner case 3: refund retried after a timeout (0:20) — slide 9

- The one from the demo. R5 needs "already refunded", and after a timeout nobody knows.
- With history, both models allowed the retry. MiniMax: "no prior refunds", 0.9 confidence.
- Ship: track pending money movements and hold the retry until a lookup shows the amount. No model.

### 10 · Where it breaks: the classifier (0:25) — slide 10

- 3 of 8 manipulative customer messages caught.
- `allow` on a possible self-harm message: no safety rule, nothing to flag. (I saw that gap while
  writing; it's the worst miss.)
- Confidence 0.99 on everything, 75% right. Useless as a threshold.
- History costs 16 points on the hardest stress cases: more context reads as permission to decide.

### 11 · Where it breaks: the measurement (0:25) — slide 11

- Same prompt, three MiniMax runs: 99, 97, 94 of 114. With history: 97, 96, 97.
- Temperature 0 doesn't fix it. So a few points between two settings is not a result.
- AI wrote the policy, the cases and the labels. Small n: ±8–9 points.

### 12 · Cost on the request path (0:15) — slide 12

- Local qwen: 9.2 s median, not usable inline. MiniMax: 2.4 s median.
- Most of it is reading the policy prompt: 10.2 s cold, 1.4 s cached.
- Ship order: deterministic checks, then a fact store, then the LLM for what's left.

### 13 · Close (0:10) — slide 13

- Next: human labels from the policy owners, the fact store, redact instead of block, repeatable scoring.
- Land it: the corner cases belong to the policy, not the model. A better model won't remove them.

## If you run long, cut this (in this order)

1. **Slide 4** (−0:15): say the one-event assumption in a sentence over slide 3.
2. **Slide 12** (−0:15): one line on latency during the close.
3. **Segment 2** (−0:10): skip the Try it demo and show slide 2 for ten seconds instead.
4. **Slide 5** (−0:10): keep only "frozen before the first run" and "κ is an upper bound".
5. **Corner case 2** down to one sentence (−0:15).

Never cut: the live conversation, corner case 1, corner case 3, slides 10 and 11.
