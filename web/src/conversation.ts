import type { Classification, Engine, TraceEvent, TurnUpdate } from "./types";

/** One event in the live transcript, with whichever guardrail verdicts have landed so far. */
export interface Row {
  event: TraceEvent;
  verdicts: Partial<Record<Engine, Classification>>;
}

/** Apply one streamed update to the transcript. Events arrive before their verdicts. */
export function applyUpdate(rows: Row[], update: TurnUpdate): Row[] {
  if (update.type === "event") {
    const next = [...rows];
    next[update.index] = { event: update.event, verdicts: {} };
    return next;
  }
  if (update.type === "verdict") {
    const row = rows[update.index];
    if (!row) return rows;
    const next = [...rows];
    next[update.index] = { ...row, verdicts: { ...row.verdicts, [update.engine]: update.classification } };
    return next;
  }
  return rows;
}
