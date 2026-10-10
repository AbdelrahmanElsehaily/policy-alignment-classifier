import { useMemo, useState } from "react";

import { api } from "../api";
import { SESSIONS, parseArguments, sessionName } from "../format";
import { EVENT_KINDS, type Classification, type EventKind, type LabeledCase, type TraceEvent } from "../types";
import { VerdictBadge, VerdictCard } from "./VerdictCard";

interface Props {
  cases: LabeledCase[];
}

export function ClassifyPanel({ cases }: Props) {
  const [kind, setKind] = useState<EventKind>("user_input");
  const [sessionKey, setSessionKey] = useState("verified as Ana (C-1001)");
  const [content, setContent] = useState("Here's my card if you need it: 4539 1488 0343 6467");
  const [toolName, setToolName] = useState("issue_refund");
  const [args, setArgs] = useState('{"order_id": "LK-50211", "amount": 250, "reason": "Item defective"}');
  const [loadedCase, setLoadedCase] = useState<LabeledCase | null>(null);
  const [llm, setLlm] = useState<Classification | null>(null);
  const [baseline, setBaseline] = useState<Classification | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const grouped = useMemo(() => {
    const groups = new Map<string, LabeledCase[]>();
    for (const c of cases) {
      const group = c.tags.includes("corner-case") ? "corner cases" : (c.tags[0] ?? "other");
      groups.set(group, [...(groups.get(group) ?? []), c]);
    }
    return [...groups.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [cases]);

  function loadCase(id: string) {
    const c = cases.find((x) => x.id === id);
    if (!c) return;
    const { event, session } = c.request;
    setKind(event.kind);
    setSessionKey(sessionName(session));
    setContent(event.content ?? "");
    setToolName(event.tool_name ?? "");
    setArgs(JSON.stringify(event.arguments ?? {}, null, 2));
    setLoadedCase(c);
    setLlm(null);
    setBaseline(null);
  }

  function buildEvent(): TraceEvent {
    if (kind === "tool_call") {
      const parsed = parseArguments(args);
      if (parsed.error) throw new Error(parsed.error);
      return { kind, tool_name: toolName, arguments: parsed.value };
    }
    if (kind === "tool_response") return { kind, tool_name: toolName, content };
    return { kind, content };
  }

  async function run() {
    setBusy(true);
    setError(null);
    setLlm(null);
    setBaseline(null);
    try {
      const body = { event: buildEvent(), session: SESSIONS[sessionKey] };
      const [llmResult, baselineResult] = await Promise.allSettled([
        api.classify(body, "llm"),
        api.classify(body, "baseline"),
      ]);
      if (llmResult.status === "fulfilled") setLlm(llmResult.value);
      else setError(`LLM classifier: ${(llmResult.reason as Error).message}`);
      if (baselineResult.status === "fulfilled") setBaseline(baselineResult.value);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const needsTool = kind === "tool_call" || kind === "tool_response";

  return (
    <section>
      <p className="hint">
        Classify one agent trace event against the Larkspur support policy. Load an eval case or write your own.
      </p>
      <div className="form">
        <label>
          Load eval case
          <select value={loadedCase?.id ?? ""} onChange={(e) => loadCase(e.target.value)}>
            <option value="">— choose —</option>
            {grouped.map(([group, items]) => (
              <optgroup key={group} label={group}>
                {items.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.id}
                  </option>
                ))}
              </optgroup>
            ))}
          </select>
        </label>
        <div className="row">
          <label>
            Event kind
            <select value={kind} onChange={(e) => setKind(e.target.value as EventKind)}>
              {EVENT_KINDS.map((k) => (
                <option key={k}>{k}</option>
              ))}
            </select>
          </label>
          <label>
            Session envelope
            <select value={sessionKey} onChange={(e) => setSessionKey(e.target.value)}>
              {Object.keys(SESSIONS).map((k) => (
                <option key={k}>{k}</option>
              ))}
            </select>
          </label>
          {needsTool && (
            <label>
              Tool
              <input value={toolName} onChange={(e) => setToolName(e.target.value)} />
            </label>
          )}
        </div>
        {kind === "tool_call" ? (
          <label>
            Arguments (JSON)
            <textarea rows={5} value={args} onChange={(e) => setArgs(e.target.value)} spellCheck={false} />
          </label>
        ) : (
          <label>
            {kind === "tool_response" ? "Tool output" : "Content"}
            <textarea rows={5} value={content} onChange={(e) => setContent(e.target.value)} />
          </label>
        )}
        <button onClick={run} disabled={busy}>
          {busy ? "Classifying…" : "Classify"}
        </button>
      </div>

      {loadedCase && (
        <div className="card gold">
          <div className="card-head">
            <strong>Gold label ({loadedCase.label_confidence})</strong>
            <VerdictBadge verdict={loadedCase.label} />
          </div>
          {loadedCase.rule_ids.length > 0 && <div className="rules">Rules: {loadedCase.rule_ids.join(", ")}</div>}
          <p className="rationale">{loadedCase.rationale}</p>
        </div>
      )}
      {error && <p className="error">{error}</p>}
      <div className="cards">
        {llm && <VerdictCard title="LLM classifier" result={llm} />}
        {baseline && <VerdictCard title="Regex baseline (flat text)" result={baseline} />}
      </div>
    </section>
  );
}
