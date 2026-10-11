import { describe, expect, it } from "vitest";

import agents from "../../data/eval/agents.jsonl?raw";
import cases from "../../data/eval/dev_test.jsonl?raw";
import titles from "../../data/ui/case_titles.json";
import hard from "../../data/eval/hard.jsonl?raw";
import stress from "../../data/eval/stress.jsonl?raw";

const ids = [cases, hard, stress, agents].flatMap((jsonl) =>
  jsonl
    .split("\n")
    .filter(Boolean)
    .map((line) => (JSON.parse(line) as { id: string }).id),
);

describe("case titles", () => {
  it("name every eval case, and nothing else", () => {
    expect(Object.keys(titles).sort()).toEqual([...ids].sort());
  });
  it("never give the answer away", () => {
    const leaks = Object.values(titles as Record<string, string>).filter((t) =>
      /\b(violation|allowed?|policy[_ ]silent)\b/i.test(t),
    );
    expect(leaks).toEqual([]);
  });
});
