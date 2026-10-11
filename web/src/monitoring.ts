/** Plain-language helpers for the Monitoring tab. */

import type { DriftStatus, FeatureDrift } from "./types";

export const FEATURES: Record<string, { name: string; about: string }> = {
  kind: { name: "Event kind", about: "Customer messages, tool calls, tool results or agent replies." },
  tool: { name: "Tool", about: "Which tool was called or answered." },
  verified: { name: "Verified customer", about: "Whether the session was verified." },
  text_length: { name: "Text length", about: "How long the message or tool payload is, in characters." },
  script: { name: "Writing system", about: "Latin, Arabic, Cyrillic, …: a proxy for language." },
  history: { name: "History length", about: "How many earlier events came with the event." },
  verdict: { name: "Verdict", about: "allow, violation or policy silent." },
  gate: { name: "Gate", about: "What the guardrail did: pass, block or hold for a human." },
  rules: { name: "Rules cited", about: "Which policy rules the classifier cited." },
  confidence: { name: "Confidence", about: "The confidence the classifier stated." },
  error: { name: "Errors", about: "Calls that failed and were held for a human." },
  latency: { name: "Model latency", about: "How long the model took per event." },
};

export const STATUS: Record<DriftStatus, { label: string; explain: string }> = {
  stable: { label: "stable", explain: "Close to the reference (PSI below 0.1)." },
  moderate: {
    label: "moderate drift",
    explain: "Noticeably different (PSI 0.1–0.25), or a category the reference never saw. Worth a look.",
  },
  major: {
    label: "major drift",
    explain: "Very different (PSI 0.25 or more). The eval numbers may no longer describe this traffic.",
  },
  insufficient: { label: "not enough data", explain: "Fewer events than needed to judge (100 by default)." },
};

/** Where a PSI value sits on a bar scaled so 0.5 fills it (larger values are capped). */
export function psiWidth(psi: number | null): number {
  return psi == null ? 0 : Math.min(psi / 0.5, 1) * 100;
}

/** "200-400 12% → 28%" for the categories that moved most. */
export function moves(f: FeatureDrift): string[] {
  return f.movers.map(([category, before, after]) => `${category} ${Math.round(before * 100)}% → ${Math.round(after * 100)}%`);
}

export function average(sum: number, n: number): number | null {
  return n ? sum / n : null;
}
