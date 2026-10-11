import { useEffect, useState } from "react";

import { api } from "../api";
import { ms } from "../format";
import { average, FEATURES, moves, psiWidth, STATUS } from "../monitoring";
import { runName, SPLITS } from "../results";
import type { DriftReport, DriftStatus, MonitoringSnapshot, RunSummary } from "../types";

const REFRESH_MS = 5000;

function StatusBadge({ status }: { status: DriftStatus }) {
  return (
    <span className={`badge drift-${status}`} title={STATUS[status].explain}>
      {STATUS[status].label}
    </span>
  );
}

function PsiBar({ psi }: { psi: number | null }) {
  return (
    <div className="psi-bar" title={psi == null ? "not measured" : `PSI ${psi.toFixed(3)}`}>
      <div className="psi-fill" style={{ width: `${psiWidth(psi)}%` }} />
      <div className="psi-mark" style={{ left: `${psiWidth(0.1)}%` }} />
      <div className="psi-mark" style={{ left: `${psiWidth(0.25)}%` }} />
    </div>
  );
}

function DriftTable({ report }: { report: DriftReport }) {
  return (
    <div>
      <p>
        Overall: <StatusBadge status={report.status} />{" "}
        <span className="small">
          {report.current_events} events compared with {report.reference_events} reference events.
        </span>
      </p>
      {(["input", "output"] as const).map((group) => (
        <div key={group}>
          <h4>
            {group === "input"
              ? "What's coming in (input drift: the world changed)"
              : "What the classifier says (output drift: the classifier changed, or the world did)"}
          </h4>
          <table className="drift-table">
            <thead>
              <tr>
                <th>Feature</th>
                <th>Shift (PSI; marks at 0.1 and 0.25)</th>
                <th>Status</th>
                <th>What moved</th>
              </tr>
            </thead>
            <tbody>
              {report.features
                .filter((f) => f.group === group)
                .map((f) => (
                  <tr key={f.feature} className={`drift-row-${f.status}`}>
                    <td>
                      <strong>{FEATURES[f.feature]?.name ?? f.feature}</strong>
                      <div className="small">{FEATURES[f.feature]?.about}</div>
                    </td>
                    <td>
                      <PsiBar psi={f.psi} />
                      <span className="small">{f.psi == null ? "–" : f.psi.toFixed(3)}</span>
                    </td>
                    <td>
                      <StatusBadge status={f.status} />
                    </td>
                    <td className="small">
                      {f.new.map((c) => (
                        <span key={c} className="new-tag" title="Never seen in the reference">
                          NEW {c}
                        </span>
                      ))}
                      {moves(f).join(" · ")}
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      ))}
    </div>
  );
}

function LiveTraffic() {
  const [snapshot, setSnapshot] = useState<MonitoringSnapshot | null>(null);
  const [auto, setAuto] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    try {
      setSnapshot(await api.monitoring());
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  useEffect(() => {
    void refresh();
    if (!auto) return;
    const timer = setInterval(() => void refresh(), REFRESH_MS);
    return () => clearInterval(timer);
  }, [auto]);

  if (error) return <p className="error">{error}</p>;
  if (!snapshot) return <p className="hint">Loading…</p>;
  const t = snapshot.totals;
  const avg = average(t.latency_ms_sum, t.events);
  const status: DriftStatus = snapshot.drift?.status ?? "insufficient";
  return (
    <div>
      <div className="row compact">
        <button className="secondary" onClick={() => void refresh()}>
          Refresh now
        </button>
        <label className="inline small">
          <input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} />
          &nbsp;refresh every {REFRESH_MS / 1000} s
        </label>
      </div>
      <div className="stat-grid">
        <div className="stat">
          <div className="stat-value">{t.events}</div>
          <div className="small">events judged since the server started</div>
        </div>
        <div className="stat">
          <div className="stat-value">
            {snapshot.window_events}/{snapshot.window_size}
          </div>
          <div className="small">in the drift window (the most recent events)</div>
        </div>
        <div className="stat">
          <div className="stat-value">
            <StatusBadge status={status} />
          </div>
          <div className="small">
            {snapshot.has_reference ? STATUS[status].explain : "No reference profile: drift can't be measured."}
          </div>
        </div>
        <div className="stat">
          <div className="stat-value">{t.errors}</div>
          <div className="small">failed calls (held for a human)</div>
        </div>
        <div className="stat">
          <div className="stat-value">{avg == null ? "–" : ms(avg)}</div>
          <div className="small">average model latency</div>
        </div>
      </div>
      {t.events > 0 && (
        <p className="small">
          Verdicts:{" "}
          {Object.entries(t.by_verdict)
            .map(([v, n]) => `${v} ${n}`)
            .join(" · ")}{" "}
          — Event kinds:{" "}
          {Object.entries(t.by_kind)
            .map(([k, n]) => `${k} ${n}`)
            .join(" · ")}
        </p>
      )}
      {snapshot.drift && snapshot.drift.status !== "insufficient" ? (
        <DriftTable report={snapshot.drift} />
      ) : (
        <p className="hint">
          {t.events === 0
            ? "No live traffic yet. Classify events (Examples, Classify an event, Live conversation) and they appear here."
            : `Drift is judged once the window holds enough events (${snapshot.window_events} so far). Meanwhile, check a recorded run below.`}
        </p>
      )}
    </div>
  );
}

function RecordedRun() {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [selected, setSelected] = useState("");
  const [report, setReport] = useState<DriftReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .runs()
      .then((all) => {
        const usable = all.filter((r) => r.classifier !== "regex-baseline" && r.repeats === 1);
        setRuns(usable);
        const stress = usable.find((r) => r.split === "stress" && !r.with_history) ?? usable[0];
        if (stress) setSelected(stress.run_id);
      })
      .catch((e: Error) => setError(e.message));
  }, []);

  useEffect(() => {
    if (!selected) return;
    setReport(null);
    setError(null);
    api
      .runDrift(selected)
      .then(setReport)
      .catch((e: Error) => setError(e.message));
  }, [selected]);

  return (
    <div>
      <label>
        Recorded run
        <select value={selected} onChange={(e) => setSelected(e.target.value)}>
          {runs.map((r) => (
            <option key={r.run_id} value={r.run_id}>
              {SPLITS[r.split]?.title ?? r.split} · {runName(r)} · {r.n_cases} events
            </option>
          ))}
        </select>
      </label>
      {error && <p className="error">{error}</p>}
      {report && <DriftTable report={report} />}
    </div>
  );
}

export function MonitoringPanel() {
  return (
    <section className="monitoring">
      <p className="hint">
        In production nobody labels the traffic, so accuracy can't be measured there, but its <b>shape</b> can. If the
        events coming in, or the verdicts going out, move away from what the classifier was evaluated on, its eval
        numbers stop describing it. Every event the classifier judges is profiled and compared with a{" "}
        <b>reference</b> (the Test-split run) using the Population Stability Index. A drifting feature logs one{" "}
        <code>drift detected</code> warning.
      </p>
      <details className="how-to-read">
        <summary>How to read drift</summary>
        <ul>
          <li>
            <b>PSI</b> measures how far a distribution has moved: below 0.1 stable, 0.1–0.25 moderate, 0.25 and above
            major.
          </li>
          <li>
            <b>NEW</b> marks a category the reference never saw, such as a new language or tool, at 2% of traffic or
            more. It always counts as at least moderate.
          </li>
          <li>
            <b>Input drift</b> means the world changed; the eval may no longer cover it. <b>Output drift</b> with stable
            inputs means the classifier changed (a new model, prompt or provider behaviour).
          </li>
          <li>Rare categories are merged, so a handful of unusual events doesn't raise a false alarm.</li>
        </ul>
      </details>

      <h3>Live traffic on this server</h3>
      <LiveTraffic />

      <h3>Check a recorded run against the reference</h3>
      <p className="small">
        The same comparison for an eval run, so drift can be seen without live traffic. The Stress run is natural
        conversations and corner cases; compare it with Test, the reference.
      </p>
      <RecordedRun />
    </section>
  );
}
