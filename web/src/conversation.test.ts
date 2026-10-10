import { describe, expect, it } from "vitest";

import { applyUpdate } from "./conversation";
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
    let rows = applyUpdate([], { type: "event", index: 0, event: { kind: "user_input", content: "hi" } });
    rows = applyUpdate(rows, { type: "event", index: 1, event: { kind: "model_output", content: "Hello" } });
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
});
