import { useEffect, useRef, useState } from "react";

import { api } from "../api";
import { applyUpdate, type Row, speaker, toHistory } from "../conversation";
import { ms } from "../format";
import type { ConversationSetup, Gate, Session, Suggestion, TraceEvent } from "../types";
import { VerdictBadge } from "./VerdictCard";

const UNVERIFIED: Session = { verified: false, verified_customer_id: null, email_on_file: null };


function EventBody({ event }: { event: TraceEvent }) {
  if (event.kind === "tool_call") {
    return (
      <code className="tool">
        {event.tool_name}({JSON.stringify(event.arguments)})
      </code>
    );
  }
  if (event.kind === "tool_response") return <pre className="event tool-result">{event.content}</pre>;
  return <p className="said">{event.content}</p>;
}

const GATE_LABEL: Record<Gate, string> = {
  pass: "passed",
  block: "blocked",
  hold: "held for human review",
};

function Verdicts({ row }: { row: Row }) {
  if (row.byGuardrail) return <span className="small">stand-in, not judged</span>;
  const llm = row.verdicts.llm;
  const baseline = row.verdicts.baseline;
  return (
    <div className="verdicts">
      {llm ? (
        <details>
          <summary>
            <VerdictBadge verdict={llm.decision.verdict} error={llm.error} />{" "}
            <span className="small">{llm.decision.rule_ids.join(", ")}</span>
          </summary>
          <p className="small rationale">{llm.error ?? llm.decision.rationale}</p>
          <p className="small">
            {llm.model} · {ms(llm.model_latency_ms)}
          </p>
        </details>
      ) : (
        <span className="small pending">guardrail judging… (event waits)</span>
      )}
      {row.gate && <span className={`gate gate-${row.gate}`}>{GATE_LABEL[row.gate]}</span>}
      {baseline && (
        <div className="small">
          regex <VerdictBadge verdict={baseline.decision.verdict} /> {baseline.decision.rule_ids.join(", ")}
        </div>
      )}
    </div>
  );
}

export function ConversationPanel() {
  const [setup, setSetup] = useState<ConversationSetup | null>(null);
  const [rows, setRows] = useState<Row[]>([]);
  const [customerId, setCustomerId] = useState("");
  const [draft, setDraft] = useState("");
  const [seesHistory, setSeesHistory] = useState(true);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const abort = useRef<AbortController | null>(null);

  useEffect(() => {
    api
      .conversation()
      .then(setSetup)
      .catch((e: Error) => setError(e.message));
    return () => abort.current?.abort();
  }, []);

  const customer = setup?.customers.find((c) => c.customer_id === customerId);
  const session: Session = customer
    ? { verified: true, verified_customer_id: customer.customer_id, email_on_file: customer.email }
    : UNVERIFIED;

  async function send() {
    const message = draft.trim();
    if (!message || busy) return;
    const controller = new AbortController();
    abort.current = controller;
    setBusy(true);
    setNotice(null);
    setError(null);
    setDraft("");
    const body = { session, history: toHistory(rows), message, guardrail_sees_history: seesHistory };
    try {
      await api.turn(
        body,
        (update) => {
          if (update.type === "stopped") setNotice(update.reason);
          else setRows((prev) => applyUpdate(prev, update));
        },
        controller.signal,
      );
    } catch (e) {
      if (!controller.signal.aborted) setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function reset() {
    abort.current?.abort();
    setRows([]);
    setNotice(null);
    setError(null);
  }

  function start(suggestion: Suggestion) {
    reset();
    setCustomerId(suggestion.verify_as ?? "");
    setDraft(suggestion.message);
  }

  if (setup && !setup.available) {
    return (
      <section>
        <p className="error">{setup.error}</p>
        <p className="hint">
          Start the server with a model, for example{" "}
          <code>PAC_BACKEND=ollama PAC_MODEL=qwen3.5 uv run pac serve --live</code>.
        </p>
      </section>
    );
  }

  const blocked = rows.filter((r) => r.gate === "block").length;
  const held = rows.filter((r) => r.gate === "hold").length;
  const judged = rows.filter((r) => !r.byGuardrail).length;
  return (
    <section>
      <p className="hint">
        Chat with Lark as a customer. Lark is a simulated agent ({setup?.agent_model ?? "…"}) with a fake back office.
        The guardrail sits <b>inline</b>: it can't change Lark, but every event waits for its verdict. Violations are{" "}
        <b>blocked</b>, policy-silent events are <b>held</b> for a human, and a stopped event is replaced by the
        guardrail's stand-in (an error for Lark, or a notice for the customer). The regex baseline is shown for
        comparison and never blocks.
      </p>
      <div className="row compact">
        <label className="inline">
          Session&nbsp;
          <select value={customerId} onChange={(e) => setCustomerId(e.target.value)} disabled={busy}>
            <option value="">not verified</option>
            {setup?.customers.map((c) => (
              <option key={c.customer_id} value={c.customer_id}>
                verified as {c.name} ({c.customer_id})
              </option>
            ))}
          </select>
        </label>
        <label className="inline">
          <input type="checkbox" checked={seesHistory} onChange={(e) => setSeesHistory(e.target.checked)} />
          &nbsp;guardrail sees the conversation so far
        </label>
        <button className="secondary" onClick={reset} disabled={rows.length === 0 && !busy}>
          New conversation
        </button>
      </div>
      <p className="small">
        Verification is set by the chat platform after a one-time code, so neither Lark nor the customer can change it.
      </p>

      <div className="suggestions">
        {setup?.suggestions.map((s) => (
          <button key={s.label} className="chip" title={s.exercises} onClick={() => start(s)} disabled={busy}>
            {s.label}
          </button>
        ))}
      </div>

      <div className="transcript">
        {rows.length === 0 && <p className="small">Pick a scenario above or type a message to start.</p>}
        {rows.map((row, i) =>
          row ? (
            <div
              key={i}
              className={`turn-row kind-${row.event.kind} ${row.byGuardrail ? "stand-in" : `gate-row-${row.gate ?? "pending"}`}`}
            >
              <div className="speaker small">{speaker(row)}</div>
              <div className="body">
                <EventBody event={row.event} />
              </div>
              <Verdicts row={row} />
            </div>
          ) : null,
        )}
        {busy && <p className="small pending">Lark is working…</p>}
      </div>
      {rows.length > 0 && (
        <p className="small">
          {judged} events judged · {blocked} blocked · {held} held for human review
        </p>
      )}
      {notice && <p className="error">{notice}</p>}
      {error && <p className="error">{error}</p>}

      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault();
          void send();
        }}
      >
        <textarea
          rows={2}
          value={draft}
          placeholder="Write as the customer…"
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void send();
            }
          }}
          disabled={busy}
        />
        <button type="submit" disabled={busy || !draft.trim()}>
          {busy ? "Waiting…" : "Send"}
        </button>
      </form>
    </section>
  );
}
