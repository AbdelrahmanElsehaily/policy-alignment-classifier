import type { Classification, Engine, Gate, HistoryEntry, TraceEvent, TurnUpdate } from "./types";

/** One event in the live transcript, with its verdicts and what the guardrail did with it. */
export interface Row {
  event: TraceEvent;
  verdicts: Partial<Record<Engine, Classification>>;
  /** Unset until the gatekeeper has judged the event. */
  gate?: Gate;
  /** The guardrail's own stand-in for an event it stopped. */
  byGuardrail: boolean;
}

/** Apply one streamed update to the transcript. Events arrive before their verdicts and gates. */
export function applyUpdate(rows: Row[], update: TurnUpdate): Row[] {
  if (update.type === "event") {
    const next = [...rows];
    next[update.index] = { event: update.event, verdicts: {}, byGuardrail: update.by_guardrail };
    return next;
  }
  if (update.type === "verdict" || update.type === "gate") {
    const row = rows[update.index];
    if (!row) return rows;
    const next = [...rows];
    next[update.index] =
      update.type === "gate"
        ? { ...row, gate: update.gate }
        : { ...row, verdicts: { ...row.verdicts, [update.engine]: update.classification } };
    return next;
  }
  return rows;
}

/** The transcript as the server needs it to rebuild the next turn. */
export function toHistory(rows: Row[]): HistoryEntry[] {
  return rows.map((r) => ({ event: r.event, gate: r.gate ?? "pass", by_guardrail: r.byGuardrail }));
}

/** Who sent an event, and to whom the guardrail's stand-ins go. */
export function speaker(row: Row): string {
  if (row.byGuardrail) return row.event.kind === "tool_response" ? "Guardrail → Lark" : "Guardrail → customer";
  switch (row.event.kind) {
    case "user_input":
      return "Customer";
    case "tool_call":
      return "Lark → tool";
    case "tool_response":
      return "Tool result";
    case "model_output":
      return "Lark";
  }
}
