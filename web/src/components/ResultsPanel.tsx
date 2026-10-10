import { useEffect, useMemo, useState } from "react";

import { api } from "../api";
import { formatRate, ms, pct } from "../format";
import { VERDICTS, type LabeledCase, type RunDetail, type RunSummary } from "../types";
import { VerdictBadge } from "./VerdictCard";

export function ResultsPanel({ cases }: { cases: LabeledCase[] }) {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [onlyErrors, setOnlyErrors] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .runs()
      .then((r) => {
        setRuns(r);
        const preferred = r.find((x) => x.split === "test" && !x.with_history && x.backend !== "regex") ?? r[0];
        if (preferred) setSelected(preferred.run_id);
      })
      .catch((e: Error) => setError(e.message));
  }, []);

  useEffect(() => {
    if (!selected) return;
    setDetail(null);
    api
      .run(selected)
      .then(setDetail)
      .catch((e: Error) => setError(e.message));
  }, [selected]);

  const caseById = useMemo(() => new Map(cases.map((c) => [c.id, c])), [cases]);

  if (error) return <p className="error">{error}</p>;
  if (runs.length === 0) return <p className="hint">No recorded runs yet. Run `uv run pac eval`.</p>;

  const m = detail?.metrics;
  const rows = detail?.predictions.filter((p) => p.repeat === 0 && (!onlyErrors || p.gold !== p.result.decision.verdict));

  return (
    <section>
      <label>
        Run
        <select value={selected} onChange={(e) => setSelected(e.target.value)}>
          {runs.map((r) => (
            <option key={r.run_id} value={r.run_id}>
              {r.split} · {r.classifier} ({r.backend}){r.with_history ? " · with history" : ""} · acc {pct(r.accuracy)} ·{" "}
              {r.run_id.slice(0, 16)}
            </option>
          ))}
        </select>
      </label>

      {detail && m && (
        <>
          <p className="hint">
            Prompt <code>{detail.config.prompt_version}</code> · policy <code>{detail.config.policy_version}</code> ·
            commit <code>{detail.config.git_commit ?? "?"}</code> · {m.n_cases} cases. Brackets are 95% intervals.
          </p>
          <div className="grid">
            <table>
              <tbody>
                <tr><th>3-way accuracy</th><td>{formatRate(m.accuracy)}</td></tr>
                <tr><th>violation recall</th><td>{formatRate(m.violation_recall)}</td></tr>
                <tr><th>violation caught or escalated</th><td>{formatRate(m.violation_caught_or_escalated)}</td></tr>
                <tr><th>violation precision</th><td>{formatRate(m.violation_precision)}</td></tr>
                <tr><th>false-block rate</th><td>{formatRate(m.false_block_rate)}</td></tr>
                <tr><th>escalation rate on allow</th><td>{formatRate(m.escalation_rate_on_allow)}</td></tr>
                <tr><th>policy-silent recall</th><td>{formatRate(m.silent_recall)}</td></tr>
                <tr><th>classifier errors</th><td>{formatRate(m.operational.classifier_errors)}</td></tr>
                <tr><th>latency p50 / p95</th><td>{ms(m.operational.model_latency_p50_ms)} / {ms(m.operational.model_latency_p95_ms)}</td></tr>
                <tr>
                  <th>cost / 1k events</th>
                  <td>{m.operational.cost_per_1k_events_usd == null ? "n/a" : `$${m.operational.cost_per_1k_events_usd.toFixed(4)}`}</td>
                </tr>
              </tbody>
            </table>
            <table>
              <thead>
                <tr>
                  <th>gold \ predicted</th>
                  {VERDICTS.map((v) => <th key={v}>{v}</th>)}
                </tr>
              </thead>
              <tbody>
                {VERDICTS.map((g) => (
                  <tr key={g}>
                    <th>{g}</th>
                    {VERDICTS.map((p) => (
                      <td key={p} className={g === p ? "diag" : ""}>{m.confusion[g][p]}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <h3>By event kind</h3>
          <table>
            <thead>
              <tr><th>kind</th><th>n</th><th>accuracy</th><th>violation recall</th><th>false-block rate</th></tr>
            </thead>
            <tbody>
              {Object.entries(m.by_kind).map(([k, s]) => (
                <tr key={k}>
                  <td><code>{k}</code></td><td>{s.n}</td><td>{formatRate(s.accuracy)}</td>
                  <td>{formatRate(s.violation_recall)}</td><td>{formatRate(s.false_block_rate)}</td>
                </tr>
              ))}
            </tbody>
          </table>

          <h3>
            Cases{" "}
            <label className="inline">
              <input type="checkbox" checked={onlyErrors} onChange={(e) => setOnlyErrors(e.target.checked)} /> only
              disagreements
            </label>
          </h3>
          <table>
            <thead>
              <tr><th>case</th><th>gold</th><th>predicted</th><th>why gold</th><th>why model</th></tr>
            </thead>
            <tbody>
              {rows?.map((p) => (
                <tr key={p.case_id}>
                  <td><code>{p.case_id}</code><div className="small">{p.kind} · {p.label_confidence}</div></td>
                  <td><VerdictBadge verdict={p.gold} /> <span className="small">{p.gold_rules.join(", ")}</span></td>
                  <td>
                    <VerdictBadge verdict={p.result.decision.verdict} />{" "}
                    <span className="small">{p.result.decision.rule_ids.join(", ")}</span>
                  </td>
                  <td className="small">{caseById.get(p.case_id)?.rationale}</td>
                  <td className="small">{p.result.error ? `error: ${p.result.error}` : p.result.decision.rationale}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </section>
  );
}
