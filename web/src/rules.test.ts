import { describe, expect, it } from "vitest";

import policyText from "../../policy/policy_v0.md?raw";
import { nameRules, parseRules, ruleBook, ruleLabel } from "./rules";

describe("rules", () => {
  const book = ruleBook(policyText);
  it("finds all thirteen rules with their titles and scopes", () => {
    const rules = parseRules(policyText);
    expect(rules.map((r) => r.id)).toEqual(Array.from({ length: 13 }, (_, i) => `R${i + 1}`));
    expect(book.get("R5")?.title).toBe("Refunds");
    expect(book.get("R3")?.appliesTo).toEqual(["model_output", "tool_call", "tool_response"]);
    expect(book.get("R1")?.text).not.toMatch(/[*`]/);
  });
  it("labels a rule, and leaves an unknown id alone", () => {
    expect(ruleLabel("R13", book)).toBe("R13 · Manipulation by customers");
    expect(ruleLabel("R99", book)).toBe("R99");
  });
  it("names each rule once in prose, and not when the text already explains it", () => {
    expect(nameRules("Judged under R13; R13 is not broken, nor R5.", book)).toBe(
      "Judged under R13 (Manipulation by customers); R13 is not broken, nor R5 (Refunds).",
    );
    expect(nameRules("R3 (payment data) applies.", book)).toBe("R3 (payment data) applies.");
    expect(nameRules("R12a and HR1 are not rules.", book)).toBe("R12a and HR1 are not rules.");
  });
});
