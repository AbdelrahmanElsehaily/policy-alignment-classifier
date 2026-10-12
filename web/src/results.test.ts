import { describe, expect, it } from "vitest";

import { mistakeOf, runName, runsForSplit, scoreBy, spread, summarise, tagValue } from "./results";
import type { Prediction, Rate, RunSummary, Verdict } from "./types";

const rate = (k: number, n: number): Rate => ({ k, n, value: n ? k / n : null, ci_low: null, ci_high: null });

const run = (over: Partial<RunSummary> = {}): RunSummary => ({
  run_id: "r",
  created_at: "",
  classifier: "qwen3.5",
  backend: "ollama",
  prompt_version: "classifier_v2@abc",
  split: "test",
  dataset: "cases.jsonl",
  n_cases: 10,
  repeats: 1,
  with_history: false,
  accuracy: rate(8, 10),
  accuracy_template_ci: null,
  violation_recall: rate(3, 4),
  false_block_rate: rate(1, 5),
  silent_recall: rate(0, 1),
  classifier_errors: rate(0, 10),
  latency_p50_ms: 900,
  accuracy_by_run: [],
  max_event_chars: null,
  ...over,
});

const pred = (gold: Verdict, said: Verdict, tags: string[] = [], error: string | null = null): Prediction => ({
  case_id: "c",
  repeat: 0,
  kind: "model_output",
  gold,
  gold_rules: [],
  label_confidence: "high",
  tags,
  result: {
    decision: { rationale: "", verdict: said, rule_ids: [], confidence: 0.9 },
    model: "m",
    prompt_version: "p",
    policy_version: "v0",
    latency_ms: 1,
    model_latency_ms: 1,
    usage: { input_tokens: 0, output_tokens: 0, cache_read_input_tokens: 0 },
    cached: false,
    error,
  },
});

describe("eval results helpers", () => {
  it("names runs for people, not for machines", () => {
    expect(runName(run())).toBe("qwen3.5 (local, 9.7B) · prompt v2");
    expect(runName(run({ with_history: true, prompt_version: "classifier_v1@x" }))).toBe(
      "qwen3.5 (local, 9.7B) · prompt v1 · with conversation history",
    );
    expect(runName(run({ classifier: "regex-baseline" }))).toBe("Regex baseline (flat text)");
    expect(runName(run({ repeats: 3, classifier: "anthropic/claude-haiku-5.5" }))).toBe("Claude Haiku 5.5 · prompt v2 · 3 runs");
    expect(spread([0.816, 0.86, 0.833])).toBe("82%–86%");
  });

  it("summarises a run in one sentence", () => {
    expect(summarise(run())).toBe(
      "Got 8 of 10 events right (80%), caught 3 of 4 violations, wrongly blocked 1 of 5 harmless events and sent 0 of 1 undecided events to a human.",
    );
    expect(summarise(run({ classifier_errors: rate(2, 10) }))).toContain("2 calls failed");
  });

  it("classifies each mistake by what it costs", () => {
    expect(mistakeOf(pred("violation", "violation"))).toBeNull();
    expect(mistakeOf(pred("violation", "allow"))).toBe("missed");
    expect(mistakeOf(pred("allow", "violation"))).toBe("false-block");
    expect(mistakeOf(pred("policy_silent", "allow"))).toBe("undecided-decided");
    expect(mistakeOf(pred("allow", "policy_silent"))).toBe("over-cautious");
    expect(mistakeOf(pred("allow", "allow", [], "timeout"))).toBe("error");
  });

  it("scores groups and reads tags", () => {
    const preds = [pred("violation", "violation", ["family:key"]), pred("allow", "violation", ["family:key"])];
    const [key] = scoreBy(preds, (p) => [tagValue(p, "family") ?? "other"]);
    expect(key).toMatchObject({ group: "key", n: 2, correct: 1, violations: 1, caught: 1, harmless: 1, blocked: 1 });
    expect(tagValue({ tags: ["ctx:10-14"] }, "ctx")).toBe("10-14");
    expect(tagValue({ tags: [] }, "ctx")).toBeNull();
  });

  it("shows the shipped prompt by default, one row per setting, the repeated run first, baseline last", () => {
    const v3 = "classifier_v3@abc";
    const runs = [
      run({ run_id: "v2", created_at: "2026-10-11T10:00" }),
      run({ run_id: "v3-once", created_at: "2026-10-12T11:00", prompt_version: v3 }),
      run({ run_id: "v3-thrice", created_at: "2026-10-12T10:00", prompt_version: v3, repeats: 3 }),
      run({ run_id: "regex", created_at: "2026-10-11T09:00", classifier: "regex-baseline" }),
      run({ run_id: "haiku", created_at: "2026-10-12T09:00", classifier: "anthropic/claude-haiku-5.5", prompt_version: v3 }),
      run({ run_id: "dev", split: "dev", prompt_version: v3 }),
    ];
    const { shown, hidden } = runsForSplit(runs, "test");
    expect(shown.map((r) => r.run_id)).toEqual(["haiku", "v3-thrice", "regex"]);
    expect(hidden).toBe(2);
    expect(runsForSplit(runs, "test", true).shown).toHaveLength(5);
  });
});
