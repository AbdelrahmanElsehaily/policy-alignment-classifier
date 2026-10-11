import { describe, expect, it } from "vitest";

import cases from "../../data/eval/dev_test.jsonl?raw";
import hard from "../../data/eval/hard.jsonl?raw";
import stress from "../../data/eval/stress.jsonl?raw";
import { CORNER_CASES } from "./cornerCases";

function ids(jsonl: string): string[] {
  return jsonl
    .split("\n")
    .filter(Boolean)
    .map((line) => (JSON.parse(line) as { id: string }).id);
}

describe("CORNER_CASES", () => {
  it("names only eval cases that exist", () => {
    const known = new Set([...ids(cases), ...ids(hard), ...ids(stress)]);
    const missing = CORNER_CASES.flatMap((c) => c.cases).filter((id) => !known.has(id));
    expect(missing).toEqual([]);
  });
});
