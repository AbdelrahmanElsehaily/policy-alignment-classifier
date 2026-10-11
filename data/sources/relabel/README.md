# Blind re-label of 30 test cases

**What:** 30 test cases drawn at random (`random.Random(2026)`) were labelled again by a separate Claude agent.
It saw only `policy_v0.md` and the events with their session envelopes (`blind_items.json`), not the gold
labels, rationales, tags or this repository. `key.json` maps each item back to its case id, and
`blind_labels.json` holds the second annotator's answers.

**Result:**

| | |
|---|---|
| Verdict agreement | 30 / 30 (Cohen's κ = 1.00; chance agreement 0.37) |
| Same rule set on violations | 10 / 10 |
| Marked `contested` | 4 by each annotator. 3 are the same cases: `r11-out-of-hours`, `r12-kb-operator-guidance`, `r13-shared-inbox-spouse` |

**How to read it, and how not to:** κ = 1.0 does **not** mean the labels are right. It is the upper bound of
what this check can show, for three reasons:

1. Both annotators are Claude, reading the same policy. Their priors are correlated, and agreement between
   two humans would almost certainly be lower.
2. The first annotator wrote each case *aiming at* a label, so the cases are cleaner than real traffic.
   Their ambiguity is the kind that was designed in.
3. The policy's verdict definitions are explicit, so a careful reader of either kind will converge on them.

What the check does show is that the labels can be **recovered from the policy text alone**. The
`policy_silent` labels in particular are not private conventions. A reader given only the policy reached
`policy_silent` for the same refund calls and order changes (items 24–26), for the same reason: the event
doesn't carry the facts the rule needs.

One point the second annotator raised that the gold rationale glosses over: on `r13-classifier-injection`,
the event doesn't show that LK-49910 belongs to someone else. Both annotators still labelled it a violation,
on the strength of the attempt to steer the classifier. The second annotator marked it `contested`; the gold
label is `high`. That is the only difference in confidence that matters.
