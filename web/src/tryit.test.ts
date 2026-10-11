import { describe, expect, it } from "vitest";

import { SESSIONS } from "./format";
import { draftOf, expectedFor, notRecorded, positionText, sameEvent } from "./tryit";
import type { Classification, LabeledCase } from "./types";

const ana = SESSIONS["verified as Ana (C-1001)"];
const refund = { kind: "tool_call" as const, tool_name: "issue_refund", arguments: { order_id: "LK-1", amount: 50 } };

function labelled(overrides: Partial<LabeledCase> = {}): LabeledCase {
  return {
    id: "c1",
    split: "test",
    request: { event: refund, session: ana, history: [{ kind: "user_input", content: "refund please" }] },
    label: "policy_silent",
    rule_ids: [],
    label_with_history: "allow",
    rule_ids_with_history: [],
    label_confidence: "high",
    rationale: "needs the order",
    tags: [],
    origin: "targeted",
    ...overrides,
  };
}

describe("sameEvent", () => {
  it("ignores argument key order", () => {
    expect(sameEvent(refund, { ...refund, arguments: { amount: 50, order_id: "LK-1" } })).toBe(true);
  });
  it("notices an edited value", () => {
    expect(sameEvent(refund, { ...refund, arguments: { order_id: "LK-1", amount: 51 } })).toBe(false);
  });
});

describe("expectedFor", () => {
  it("gives the label while the event is unchanged, and the history label when asked", () => {
    expect(expectedFor(labelled(), refund, ana, false)?.verdict).toBe("policy_silent");
    expect(expectedFor(labelled(), refund, ana, true)?.verdict).toBe("allow");
  });
  it("drops the label once the event or the session is edited", () => {
    expect(expectedFor(labelled(), { ...refund, arguments: { order_id: "LK-2", amount: 50 } }, ana, false)).toBeNull();
    expect(expectedFor(labelled(), refund, SESSIONS.unverified, false)).toBeNull();
  });
});

describe("positionText", () => {
  it("uses what was said, or an email body for a tool call", () => {
    expect(positionText({ kind: "user_input", content: "hi", toolName: "", args: "{}" })).toBe("hi");
    const email = draftOf({ kind: "tool_call", tool_name: "send_email", arguments: { body: "the card" } });
    expect(positionText(email)).toBe("the card");
    expect(positionText(draftOf({ kind: "tool_call", tool_name: "issue_refund", arguments: { amount: 5 } }))).toBe("");
  });
});

describe("notRecorded", () => {
  it("recognises a replay miss", () => {
    const miss = { error: "no recorded completion for this request under model x" } as Classification;
    expect(notRecorded(miss)).toBe(true);
    expect(notRecorded(undefined)).toBe(false);
  });
});
