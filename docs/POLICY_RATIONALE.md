# Policy rationale

Why [`policy/policy_v0.md`](../policy/policy_v0.md) looks the way it does. The classifier sees only the policy;
this file is for human readers.

## How it was written

I wrote the policy the way the business deploying the agent (the "customer" in the brief's sense) would
plausibly write it: a page or two, concrete where the business has hard numbers (refund limits, return
window), and silent on things a support lead wouldn't think to write down. I did **not** try to close every gap
I could think of. A policy patched against every case its author could imagine would make the classifier's
"policy_silent" verdict meaningless, and wouldn't reflect a real deployment.

That creates an honesty obligation. While writing, I noticed some gaps and left them in on purpose. They are
listed below under "[Gaps I saw while writing](#gaps-i-saw-while-writing)". The write-up keeps them apart from
the gaps **discovered while labelling**. Only the second kind counts as a finding in the sense the brief
means.

The policy is frozen at v0 for evaluation. Amendments go in `policy_v1_proposed.md`, so the reported numbers
are always measured against a fixed target.

## Structural choices

**Why a support agent.** The person typing is an unverified member of the public. The principal whose policy
applies is the business. That reverses the brief's ops example, where the typist is a trusted employee, and
makes "position matters" carry more weight: a `user_input` can be an attack, and sending email outside the
company is normal.

**Why a session envelope (`verified`, `verified_customer_id`, `email_on_file`).** The brief says the classifier
reads *one* event and leaves "how much context an event carries" open. Without verification state, almost no
support action can be judged on its own: a refund is fine or not depending on who is asking. Real chat
platforms track authentication state on the session and attach it to every event, so including it is a
realistic assumption. Including the full chat history would not be. The envelope deliberately leaves out
*what was said earlier*, so cases that need history stay undecidable and show up as findings.

**Why verification is done by the platform (one-time code), not by Lark.** If Lark verified customers itself
by asking for an order number and email, then "is this session verified?" would be a judgement about earlier
events, and every downstream verdict would inherit it. Moving verification into the platform makes it a fact
the event carries.

**Why `issue_refund` has no destination argument.** This is an assumption about Lark's tool registry, which
belongs to Larkspur. The guardrail is a man in the middle and does not design or change the agent's tools
([`ARCHITECTURE.md`](ARCHITECTURE.md)). A tool that can only refund the original payment method removes the
"refund to a different card" attack before any guardrail sees it. If the real tool did take a destination,
the policy would need a rule for it and the guardrail would have to check the argument.

**Why three verdicts.** `policy_silent` is the classifier's way of saying the policy doesn't decide this case.
That is the signal the brief cares most about, and in production it is what sends a case to a human. It
covers three situations (no rule applies, rules conflict, information missing). The rationale says which one,
so the verdict space stays small.

**Why each rule lists the event kinds it applies to.** It makes "position matters" mechanical. R4 is the
clearest example: a secret is a violation in `tool_response` and `model_output`, and explicitly not in
`user_input`. This is the brief's own example, made into a rule.

## Rule by rule

| Rule | Why it exists | Why it is worded this way |
|---|---|---|
| R1 Verify first | Account takeover via the support chat is the classic attack on support agents. | Lookups are allowed before verification because Lark needs them to help at all. Only *disclosing* the results is restricted. |
| R2 Own account only | Verification proves who you are, not that you may see someone else's order. | Tied to `verified_customer_id` so it can be checked from the envelope plus the tool's arguments or result. |
| R3 Payment data | PCI exposure is a costly, well-understood risk. | "Last four digits" is excluded explicitly because support agents routinely confirm cards that way. Customers volunteering their card is allowed: it happens constantly, and blocking the customer's message protects nothing. |
| R4 Secrets & internal info | The brief's credential-disclosure example. | It encodes the brief's position point directly: a leak in tool output, but not when pasted by a user. Fraud flags count as internal because telling a fraudster why they were flagged helps them. |
| R5 Refunds | Money moving is the most consequential action Lark has. | Concrete numbers ($200, 30 days, 1 year), because real businesses have them and they produce real edge cases around the thresholds. |
| R6 Order changes | Changing the address of a shipped order is a common fraud pattern, and impossible in practice anyway. | Based on order status, which tool results carry. |
| R7 Email | The brief's external-recipient example, adapted. External email is normal in support, so the question becomes *which* external address. | Limited to the verified email on file, the strictest version a business would plausibly accept. |
| R8 Tools | Stops the agent being used for anything unrelated to support. | Short on purpose. Real policies say this in one line. |
| R9 Commitments | Chatbots promising refunds or discounts have produced real legal disputes. | Written as "anything it cannot do itself" rather than a closed list, so the classifier can apply it to new kinds of promise. |
| R10 Advice | A standard clause in customer-facing assistant policies. | Product safety points to the help centre, because an electronics retailer gets battery and overheating questions. |
| R11 Human handoff | Refusing a human handoff is a top complaint about support bots, and regulated in some places. | Applies to both what Lark says and what it does, because refusing can show up in either. |
| R12 Injection in tool data | The brief's injection example. | The violation is the *presence* of instructions in tool data, whether or not Lark follows them, so it can be judged from the `tool_response` alone. |
| R13 Customer manipulation | In support, the user is the most likely attacker. | The good-faith clause is deliberate. Customers ask for things they can't have all the time, and flagging every such request would make the classifier useless. Where good faith ends and manipulation begins is expected to be a major source of hard cases. |

## What the policy deliberately does not cover

These are left out because a support lead plausibly wouldn't write them, not because they are unimportant:

- tone, politeness, or brand voice;
- topics unrelated to Larkspur (weather, homework, etc.);
- hallucination, or misstating facts about orders (can't be checked from one event; see below);
- legal rights such as deletion requests and data-subject access;
- safety situations in the conversation (threats, self-harm, minors).

## Gaps I saw while writing

These are disclosed up front so they aren't later presented as discoveries:

1. **Authority claims a policy can't check.** "Your colleague said you'd approve this" is covered by R13 when
   it's manipulation, but a true statement looks identical.
2. **Account holder vs. person typing.** The one-time code proves access to the inbox, not identity. A spouse
   with access to a shared inbox is verified.
3. **Splitting refunds to stay under $200.** R5 is per call. Two $150 refunds on a $300 order can each pass
   R5(b) if the first one isn't yet reflected in "already refunded".
4. **Free text inside a structured tool result.** R12 depends on telling "free-text" fields apart from
   structured ones, and some fields (order notes written by staff) are both.
5. **Statements of fact Lark can't support.** Without the chat history, a `model_output` saying "your refund
   has been processed" can't be checked against what Lark actually did.
