import { describe, expect, it } from "vitest";

import { applyUpdate, speaker, toHistory } from "./conversation";
import type { Classification } from "./types";

const verdict = (v: "allow" | "violation"): Classification => ({
  decision: { rationale: "r", verdict: v, rule_ids: v === "violation" ? ["R4"] : [], confidence: 0.9 },
  model: "m",
  prompt_version: "p",
  policy_version: "v0",
  latency_ms: 1,
  model_latency_ms: 1,
  usage: { input_tokens: 0, output_tokens: 0, cache_read_input_tokens: 0 },
  cached: false,
  error: null,
});

describe("live transcript", () => {
  it("adds events, then attaches each engine's verdict to its event", () => {
    let rows = applyUpdate([], {
      type: "event",
      index: 0,
      event: { kind: "user_input", content: "hi" },
      by_guardrail: false,
    });
    rows = applyUpdate(rows, {
      type: "event",
      index: 1,
      event: { kind: "model_output", content: "Hello" },
      by_guardrail: false,
    });
    rows = applyUpdate(rows, { type: "verdict", index: 1, engine: "llm", classification: verdict("violation") });
    rows = applyUpdate(rows, { type: "verdict", index: 1, engine: "baseline", classification: verdict("allow") });

    expect(rows).toHaveLength(2);
    expect(rows[0].verdicts).toEqual({});
    expect(rows[1].verdicts.llm?.decision.verdict).toBe("violation");
    expect(rows[1].verdicts.baseline?.decision.verdict).toBe("allow");
  });

  it("ignores verdicts for unknown events and non-event updates", () => {
    const rows = applyUpdate([], { type: "verdict", index: 3, engine: "llm", classification: verdict("allow") });
    expect(rows).toEqual([]);
    expect(applyUpdate(rows, { type: "done" })).toBe(rows);
    expect(applyUpdate(rows, { type: "stopped", reason: "x" })).toBe(rows);
  });

  it("records the gate, and sends stopped events and stand-ins back as history", () => {
    let rows = applyUpdate([], {
      type: "event",
      index: 0,
      event: { kind: "tool_call", tool_name: "issue_refund", arguments: { amount: 349 } },
      by_guardrail: false,
    });
    rows = applyUpdate(rows, { type: "gate", index: 0, gate: "block" });
    rows = applyUpdate(rows, {
      type: "event",
      index: 1,
      event: { kind: "tool_response", tool_name: "issue_refund", content: '{"error": "blocked"}' },
      by_guardrail: true,
    });
    rows = applyUpdate(rows, { type: "event", index: 2, event: { kind: "model_output", content: "Hi" }, by_guardrail: false });

    expect(toHistory(rows).map((h) => [h.gate, h.by_guardrail])).toEqual([
      ["block", false],
      ["pass", true],
      ["pass", false],
    ]);
    expect(rows.map(speaker)).toEqual(["Lark → tool", "Guardrail → Lark", "Lark"]);
    expect(speaker({ event: { kind: "model_output", content: "[x]" }, verdicts: {}, byGuardrail: true })).toBe(
      "Guardrail → customer",
    );
  });
});
