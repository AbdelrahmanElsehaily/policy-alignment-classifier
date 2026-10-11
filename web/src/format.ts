import type { EventKind, Rate, Session, TraceEvent } from "./types";

/** One line for an event: what was said, or which tool was called with what. */
export function describeEvent(event: TraceEvent): string {
  if (event.kind === "tool_call") return `${event.tool_name}(${JSON.stringify(event.arguments)})`;
  if (event.kind === "tool_response") return `${event.tool_name} → ${event.content ?? ""}`;
  return event.content ?? "";
}

export function pct(value: number | null | undefined, digits = 0): string {
  return value == null ? "n/a" : `${(value * 100).toFixed(digits)}%`;
}

export function formatRate(rate: Rate): string {
  if (rate.value == null || rate.ci_low == null || rate.ci_high == null) return "n/a";
  return `${pct(rate.value)} [${pct(rate.ci_low)}–${pct(rate.ci_high)}] (${rate.k}/${rate.n})`;
}

export function ms(value: number | null | undefined): string {
  if (value == null) return "n/a";
  return value >= 1000 ? `${(value / 1000).toFixed(1)} s` : `${Math.round(value)} ms`;
}

export const SESSIONS: Record<string, Session> = {
  unverified: { verified: false, verified_customer_id: null, email_on_file: null },
  "verified as Ana (C-1001)": {
    verified: true,
    verified_customer_id: "C-1001",
    email_on_file: "ana.ruiz@mail.example",
  },
  "verified as Ben (C-1002)": {
    verified: true,
    verified_customer_id: "C-1002",
    email_on_file: "ben.okoro@mail.example",
  },
};

export function sessionName(session: Session): string {
  const match = Object.entries(SESSIONS).find(
    ([, s]) => s.verified === session.verified && s.verified_customer_id === session.verified_customer_id,
  );
  return match ? match[0] : "unverified";
}

/** Place the same text in each event kind, as the position demo does. */
export function eventForKind(kind: EventKind, text: string, session: Session): TraceEvent {
  switch (kind) {
    case "user_input":
    case "model_output":
      return { kind, content: text };
    case "tool_response":
      return { kind, tool_name: "search_kb", content: JSON.stringify({ title: "Help article", body: text }) };
    case "tool_call":
      return {
        kind,
        tool_name: "send_email",
        arguments: {
          to: session.email_on_file ?? "support@larkspur.example",
          subject: "Your request",
          body: text,
        },
      };
  }
}

/** Parse the tool-call arguments textarea; returns an error message instead of throwing. */
export function parseArguments(text: string): { value?: Record<string, unknown>; error?: string } {
  try {
    const value: unknown = JSON.parse(text || "{}");
    if (value === null || typeof value !== "object" || Array.isArray(value)) {
      return { error: "Arguments must be a JSON object" };
    }
    return { value: value as Record<string, unknown> };
  } catch (e) {
    return { error: `Invalid JSON: ${(e as Error).message}` };
  }
}
