import { describe, expect, it } from "vitest";

import { SESSIONS, eventForKind, formatRate, ms, parseArguments, pct, sessionName } from "./format";

describe("formatting", () => {
  it("formats rates with intervals", () => {
    expect(formatRate({ k: 8, n: 10, value: 0.8, ci_low: 0.49, ci_high: 0.94 })).toBe("80% [49%–94%] (8/10)");
    expect(formatRate({ k: 0, n: 0, value: null, ci_low: null, ci_high: null })).toBe("n/a");
    expect(pct(null)).toBe("n/a");
  });

  it("formats latency", () => {
    expect(ms(420)).toBe("420 ms");
    expect(ms(2500)).toBe("2.5 s");
    expect(ms(null)).toBe("n/a");
  });
});

describe("events", () => {
  const ana = SESSIONS["verified as Ana (C-1001)"];

  it("places text in every kind", () => {
    expect(eventForKind("user_input", "x", ana)).toEqual({ kind: "user_input", content: "x" });
    expect(eventForKind("tool_call", "x", ana).arguments).toMatchObject({ to: "ana.ruiz@mail.example", body: "x" });
    expect(JSON.parse(eventForKind("tool_response", "x", ana).content ?? "")).toMatchObject({ body: "x" });
  });

  it("names sessions", () => {
    expect(sessionName(ana)).toBe("verified as Ana (C-1001)");
    expect(sessionName(SESSIONS.unverified)).toBe("unverified");
  });

  it("parses tool arguments safely", () => {
    expect(parseArguments('{"order_id": "LK-1"}').value).toEqual({ order_id: "LK-1" });
    expect(parseArguments("[1]").error).toMatch(/object/);
    expect(parseArguments("{").error).toMatch(/Invalid JSON/);
  });
});
