import { describe, expect, it } from "vitest";

import { API_VERSION, isStaleServer } from "./api";
import type { Health } from "./types";

const health = (api_version?: number): Health => ({
  api_version,
  backend: "ollama",
  model: "qwen3.5",
  live: true,
  llm_available: true,
  llm_error: null,
});

describe("server version check", () => {
  it("flags a server older than the page, including one without a version", () => {
    expect(isStaleServer(health(API_VERSION))).toBe(false);
    expect(isStaleServer(health(API_VERSION - 1))).toBe(true);
    expect(isStaleServer(health(undefined))).toBe(true);
  });
});
