import { describe, expect, it } from "vitest";

import { average, FEATURES, moves, psiWidth, STATUS } from "./monitoring";
import type { FeatureDrift } from "./types";

describe("monitoring helpers", () => {
  it("scales PSI onto a bar and caps it", () => {
    expect(psiWidth(null)).toBe(0);
    expect(psiWidth(0.1)).toBeCloseTo(20);
    expect(psiWidth(0.25)).toBeCloseTo(50);
    expect(psiWidth(2)).toBe(100);
  });

  it("describes what moved", () => {
    const f: FeatureDrift = {
      feature: "script",
      group: "input",
      psi: 0.06,
      status: "moderate",
      movers: [["arabic", 0, 0.031]],
      new: ["arabic"],
    };
    expect(moves(f)).toEqual(["arabic 0% → 3%"]);
  });

  it("names every feature and status", () => {
    for (const feature of ["kind", "tool", "verified", "text_length", "script", "history"]) {
      expect(FEATURES[feature].name).toBeTruthy();
    }
    expect(Object.keys(STATUS)).toEqual(["stable", "moderate", "major", "insufficient"]);
    expect(average(10, 0)).toBeNull();
    expect(average(10, 4)).toBe(2.5);
  });
});
