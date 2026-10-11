/** Pure helpers for the Try it tab: the editor's event, expected labels, and the position view. */
import type { Classification, EventKind, LabeledCase, Session, TraceEvent, Verdict } from "./types";

export const KIND_LABELS: Record<EventKind, string> = {
  user_input: "Customer message",
  model_output: "Agent reply",
  tool_call: "Tool call",
  tool_response: "Tool result",
};

/** What the editor holds. Tool name and arguments only matter for tool events. */
export interface Draft {
  kind: EventKind;
  content: string;
  toolName: string;
  args: string;
}

export function draftOf(event: TraceEvent): Draft {
  return {
    kind: event.kind,
    content: event.content ?? "",
    toolName: event.tool_name ?? "",
    args: JSON.stringify(event.arguments ?? {}, null, 2),
  };
}

/** True when two events say the same thing (key order and whitespace in arguments ignored). */
export function sameEvent(a: TraceEvent, b: TraceEvent): boolean {
  const norm = (e: TraceEvent) =>
    JSON.stringify({
      kind: e.kind,
      content: e.content ?? null,
      tool_name: e.kind === "tool_call" || e.kind === "tool_response" ? (e.tool_name ?? null) : null,
      arguments: e.arguments ? Object.fromEntries(Object.entries(e.arguments).sort()) : null,
    });
  return norm(a) === norm(b);
}

export function sameSession(a: Session, b: Session): boolean {
  return a.verified === b.verified && a.verified_customer_id === b.verified_customer_id;
}

/** The label to compare against, or null once the event or session has been edited. */
export function expectedFor(
  loaded: LabeledCase | null,
  event: TraceEvent | null,
  session: Session,
  withHistory: boolean,
): { verdict: Verdict; rules: string[] } | null {
  if (!loaded || !event) return null;
  if (!sameEvent(loaded.request.event, event) || !sameSession(loaded.request.session, session)) return null;
  if (withHistory && loaded.label_with_history) {
    return { verdict: loaded.label_with_history, rules: loaded.rule_ids_with_history };
  }
  return { verdict: loaded.label, rules: loaded.rule_ids };
}

/** The text to place in all four positions: what was said, or a tool call's email body.
 * Empty for a tool call without one, since its arguments aren't something a person says. */
export function positionText(draft: Draft): string {
  if (draft.kind !== "tool_call") return draft.content;
  try {
    const args = JSON.parse(draft.args) as Record<string, unknown>;
    return typeof args.body === "string" ? args.body : "";
  } catch {
    return "";
  }
}

/** Replay mode has no answer for text nobody recorded; that isn't a classifier failure. */
export function notRecorded(result: Classification | undefined): boolean {
  return Boolean(result?.error?.includes("no recorded completion"));
}
