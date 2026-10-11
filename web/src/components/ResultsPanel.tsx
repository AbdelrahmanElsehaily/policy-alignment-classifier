import { Fragment, useEffect, useMemo, useState } from "react";

import { api } from "../api";
import { describeEvent, ms } from "../format";
import {
  contextOf,
  fraction,
  type GroupScore,
  MISTAKES,
  type MistakeKind,
  mistakeOf,
  percent,
  range,
  runModel,
  runName,
  runSetting,
  runsForSplit,
  SPLIT_ORDER,
  SPLITS,
  scoreBy,
  summarise,
  tagValue,
} from "../results";
import type { LabeledCase, Prediction, RunDetail, RunSummary, Verdict } from "../types";
import { VerdictBadge } from "./VerdictCard";

const VERDICT_LABEL: Record<Verdict, string> = {
  allow: "allow",
  violation: "violation",
  policy_silent: "policy silent",
};

const COLUMNS = [
  { name: "Accuracy", help: "Events where the verdict matched the label: allow, violation or policy silent." },
  { name: "Violations caught", help: "Of the events that break a rule, how many it called a violation. Higher is better." },
  { name: "Harmless events blocked", help: "Of the events that break no rule, how many it called a violation. Lower is better." },
  {
    name: "Undecided sent to a human",
    help: "Of the events the policy doesn't decide, how many it marked policy silent. Higher is better.",
  },
  { name: "Errors", help: "Calls that failed. They count as wrong; the guardrail holds them for a human." },
  { name: "Median latency", help: "Time the model took per event (recorded when the answer was first made)." },
];

type Breakdown = "kind" | "family" | "context" | "confidence";

const BREAKDOWNS: Record<Breakdown, { label: string; explain: string; groupsOf: (p: Prediction) => string[] }> = {
  kind: {
    label: "Event kind",
    explain: "Where the event sits in the trace: the customer's message, a tool call, a tool result, or the agent's reply.",
    groupsOf: (p) => [p.kind],
  },
  family: {
    label: "Case family",
    explain: "Which corner case or conversation source the event belongs to.",
    groupsOf: (p) => {
      const family = tagValue(p, "family");
      return family ? [family] : [];
    },
  },
  context: {
    label: "Context length",
    explain: "Conversation turns only, by how many events came before them.",
    groupsOf: (p) => {
      const ctx = tagValue(p, "ctx");
      return ctx ? [`${ctx} earlier events`] : [];
    },
  },
  confidence: {
    label: "Label certainty",
    explain: "How sure the label itself is. 'Contested' labels are judgement calls; agreement there means agreeing with my reading.",
    groupsOf: (p) => [p.label_confidence],
  },
};

function RunTable({
  runs,
  selected,
  onSelect,
}: {
  runs: RunSummary[];
  selected: string;
  onSelect: (id: string) => void;
}) {
  return (
    <table className="results-table">
      <thead>
        <tr>
          <th>Classifier</th>
          {COLUMNS.map((c) => (
            <th key={c.name} title={c.help}>
              {c.name}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {runs.map((r) => {
          const ci = r.accuracy_template_ci ?? [r.accuracy.ci_low, r.accuracy.ci_high];
          return (
            <tr
              key={r.run_id}
              className={r.run_id === selected ? "selected-row" : "clickable-row"}
              onClick={() => onSelect(r.run_id)}
            >
              <td>
                <strong>{runModel(r)}</strong>
                {runSetting(r) && <div className="small">{runSetting(r)}</div>}
              </td>
              <td>
                <strong>{percent(r.accuracy.value)}</strong>{" "}
                <span className="small">
                  {r.accuracy.k}/{r.accuracy.n} · range {range(ci[0], ci[1])}
                </span>
              </td>
              <td>{fraction(r.violation_recall)}</td>
              <td>{fraction(r.false_block_rate)}</td>
              <td>{fraction(r.silent_recall)}</td>
              <td>{r.classifier_errors.k ? fraction(r.classifier_errors) : "none"}</td>
              <td>{r.classifier === "regex-baseline" ? "instant" : ms(r.latency_p50_ms)}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function HowToRead({ split }: { split: string }) {
  return (
    <details className="how-to-read">
      <summary>How to read these numbers</summary>
      <ul>
        {COLUMNS.map((c) => (
          <li key={c.name}>
            <b>{c.name}:</b> {c.help}
          </li>
        ))}
        <li>
          <b>Range:</b> where the true rate plausibly lies (95%), given how few events there are. Wide range, few
          events: don't read much into small differences.
          {split === "stress" &&
            " On Stress, related events share a template (a conversation, a phrasing), so the range resamples whole templates and is wider than a per-event range."}
        </li>
        <li>
          <b>The three verdicts:</b> <i>allow</i> passes the event, <i>violation</i> blocks it, <i>policy silent</i>{" "}
          means the policy doesn't decide it, so the guardrail holds it for a human.
        </li>
        <li>
          <b>Event only vs with conversation history:</b> the classifier sees just the event, or the event plus
          everything earlier in the conversation.
        </li>
      </ul>
    </details>
  );
}

function Confusion({ detail }: { detail: RunDetail }) {
  const verdicts: Verdict[] = ["allow", "violation", "policy_silent"];
  const m = detail.metrics;
  const cost = (gold: Verdict, said: Verdict | "error") =>
    gold === said ? "diag" : gold === "violation" && said === "allow" ? "costly" : gold === "allow" && said === "violation" ? "costly" : "";
  return (
    <div>
      <h4>Every event, by right answer and classifier answer</h4>
      <table className="confusion">
        <thead>
          <tr>
            <th>Right answer ↓ · Classifier said →</th>
            {verdicts.map((v) => (
              <th key={v}>{VERDICT_LABEL[v]}</th>
            ))}
            <th>error</th>
          </tr>
        </thead>
        <tbody>
          {verdicts.map((g) => (
            <tr key={g}>
              <th>{VERDICT_LABEL[g]}</th>
              {[...verdicts, "error" as const].map((said) => (
                <td key={said} className={cost(g, said)}>
                  {m.confusion[g]?.[said] ?? 0}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="small">
        Green: correct. Red: the costly mistakes, a missed violation (violation → allow) or a harmless event blocked
        (allow → violation).
      </p>
    </div>
  );
}

function BreakdownTable({ predictions }: { predictions: Prediction[] }) {
  const available = (Object.keys(BREAKDOWNS) as Breakdown[]).filter(
    (b) => b === "kind" || b === "confidence" || predictions.some((p) => BREAKDOWNS[b].groupsOf(p).length),
  );
  const [by, setBy] = useState<Breakdown>("kind");
  const current = available.includes(by) ? by : "kind";
  const rows: GroupScore[] = scoreBy(predictions, BREAKDOWNS[current].groupsOf).sort((a, b) =>
    current === "context" ? a.group.localeCompare(b.group, undefined, { numeric: true }) : b.n - a.n,
  );
  return (
    <div>
      <h4>Where it does well and badly</h4>
      <div className="row compact">
        {available.map((b) => (
          <button key={b} className={b === current ? "chip active-chip" : "chip"} onClick={() => setBy(b)}>
            {BREAKDOWNS[b].label}
          </button>
        ))}
      </div>
      <p className="small">{BREAKDOWNS[current].explain}</p>
      <table>
        <thead>
          <tr>
            <th>{BREAKDOWNS[current].label}</th>
            <th>Events</th>
            <th>Accuracy</th>
            <th>Violations caught</th>
            <th>Harmless events blocked</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((g) => (
            <tr key={g.group}>
              <td>
                <code>{g.group}</code>
              </td>
              <td>{g.n}</td>
              <td>
                {percent(g.correct / g.n)} <span className="small">({g.correct}/{g.n})</span>
              </td>
              <td>{g.violations ? `${g.caught}/${g.violations}` : "–"}</td>
              <td>{g.harmless ? `${g.blocked}/${g.harmless}` : "–"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function MistakeDetail({ p, c }: { p: Prediction; c: LabeledCase | undefined }) {
  return (
    <div className="mistake-detail">
      <pre className="event">{c ? describeEvent(c.request.event) : "(event not found)"}</pre>
      <div className="two-col">
        <div>
          <div className="small">Right answer</div>
          <VerdictBadge verdict={p.gold} /> <span className="small">{p.gold_rules.join(", ")}</span>
          <p className="small">{c?.rationale}</p>
        </div>
        <div>
          <div className="small">Classifier said</div>
          <VerdictBadge verdict={p.result.decision.verdict} error={p.result.error} />{" "}
          <span className="small">{p.result.decision.rule_ids.join(", ")}</span>
          <p className="small">{p.result.error ?? p.result.decision.rationale}</p>
        </div>
      </div>
    </div>
  );
}

function Mistakes({ predictions, caseById }: { predictions: Prediction[]; caseById: Map<string, LabeledCase> }) {
  const counts = new Map<MistakeKind, number>();
  for (const p of predictions) {
    const k = mistakeOf(p);
    if (k) counts.set(k, (counts.get(k) ?? 0) + 1);
  }
  const kinds = (Object.keys(MISTAKES) as MistakeKind[]).filter((k) => counts.get(k));
  const total = [...counts.values()].reduce((a, b) => a + b, 0);
  const [shown, setShown] = useState<MistakeKind | "all">("all");
  const current = shown === "all" || counts.get(shown) ? shown : "all";
  const [limit, setLimit] = useState(25);
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set());
  function toggle(id: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (!next.delete(id)) next.add(id);
      return next;
    });
  }
  const severity = Object.keys(MISTAKES) as MistakeKind[]; // costliest first
  const rows = predictions
    .filter((p) => {
      const k = mistakeOf(p);
      return k !== null && (current === "all" || k === current);
    })
    .sort((a, b) => severity.indexOf(mistakeOf(a)!) - severity.indexOf(mistakeOf(b)!));
  return (
    <div>
      <h3>Its mistakes</h3>
      <p className="small">One row per mistake, costliest first. Click a row to see the event and both explanations.</p>
      {kinds.length === 0 ? (
        <p className="hint">No mistakes on this set.</p>
      ) : (
        <>
          <div className="row compact">
            <button className={current === "all" ? "chip active-chip" : "chip"} onClick={() => setShown("all")}>
              All mistakes ({total})
            </button>
            {kinds.map((k) => (
              <button key={k} className={k === current ? "chip active-chip" : "chip"} onClick={() => setShown(k)}>
                {MISTAKES[k].label} ({counts.get(k)})
              </button>
            ))}
          </div>
          {current !== "all" && <p className="small">{MISTAKES[current].explain}</p>}
          <table className="mistakes-table">
            <thead>
              <tr>
                <th>Case</th>
                <th>Event</th>
                <th>Right answer</th>
                <th>Classifier said</th>
                <th>Mistake</th>
              </tr>
            </thead>
            <tbody>
              {rows.slice(0, limit).map((p) => {
                const isOpen = expanded.has(p.case_id);
                const kind = mistakeOf(p);
                const c = caseById.get(p.case_id);
                return (
                  <Fragment key={p.case_id}>
                    <tr
                      className={`clickable-row mistake-row mistake-${kind ?? "none"}${isOpen ? " selected-row" : ""}`}
                      onClick={() => toggle(p.case_id)}
                      title="Click to see the event and both explanations"
                    >
                      <td>
                        {isOpen ? "▾" : "▸"} <code>{p.case_id}</code>
                      </td>
                      <td className="small">
                        <code>{p.kind}</code> · {contextOf(c)}
                      </td>
                      <td>
                        <VerdictBadge verdict={p.gold} />
                      </td>
                      <td>
                        <VerdictBadge verdict={p.result.decision.verdict} error={p.result.error} />
                      </td>
                      <td className="small">{kind ? MISTAKES[kind].label : "correct"}</td>
                    </tr>
                    {isOpen && (
                      <tr className="mistake-open">
                        <td colSpan={5}>
                          <MistakeDetail p={p} c={c} />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
          {rows.length > limit && (
            <button className="secondary" onClick={() => setLimit(limit + 25)}>
              Show more ({rows.length - limit} left)
            </button>
          )}
        </>
      )}
    </div>
  );
}

function RunDetails({ summary, caseById }: { summary: RunSummary; caseById: Map<string, LabeledCase> }) {
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setDetail(null);
    api
      .run(summary.run_id)
      .then(setDetail)
      .catch((e: Error) => setError(e.message));
  }, [summary.run_id]);

  if (error) return <p className="error">{error}</p>;
  if (!detail) return <p className="hint">Loading the run…</p>;
  const predictions = detail.predictions.filter((p) => p.repeat === 0);
  const config = detail.config;
  return (
    <div className="run-details">
      <h3>{runName(summary)}</h3>
      <p className="lead">{summarise(summary)}</p>
      <div className="grid">
        <Confusion detail={detail} />
        <BreakdownTable predictions={predictions} />
      </div>
      <Mistakes predictions={predictions} caseById={caseById} />
      <details>
        <summary>Where this run came from</summary>
        <ul className="small">
          <li>
            Run <code>{config.run_id}</code>, recorded {new Date(config.created_at).toLocaleString()}
          </li>
          <li>
            Prompt <code>{config.prompt_version}</code> · policy <code>{config.policy_version}</code> · dataset{" "}
            <code>{config.dataset}</code> (<code>{config.dataset_sha256.slice(0, 12)}</code>)
          </li>
          <li>
            Code at commit <code>{config.git_commit ?? "unknown"}</code> · backend <code>{config.backend}</code>
          </li>
          <li>
            Every answer is cached, so <code>uv run pac report results/runs/{config.run_id}</code> rebuilds this run's
            report offline, and CI replays it on every push.
          </li>
        </ul>
      </details>
    </div>
  );
}

export function ResultsPanel({ cases }: { cases: LabeledCase[] }) {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [split, setSplit] = useState("test");
  const [selected, setSelected] = useState("");
  const [includeOlder, setIncludeOlder] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .runs()
      .then(setRuns)
      .catch((e: Error) => setError(e.message));
  }, []);

  const caseById = useMemo(() => new Map(cases.map((c) => [c.id, c])), [cases]);
  const splits = SPLIT_ORDER.filter((s) => runs.some((r) => r.split === s));
  const { shown, hidden } = useMemo(() => runsForSplit(runs, split, includeOlder), [runs, split, includeOlder]);
  const current = shown.find((r) => r.run_id === selected) ?? shown.find((r) => r.classifier !== "regex-baseline") ?? shown[0];

  if (error) return <p className="error">{error}</p>;
  if (runs.length === 0) return <p className="hint">No eval runs yet. Create one with `uv run pac eval`.</p>;

  const info = SPLITS[split];
  const nCases = shown[0]?.n_cases;
  return (
    <section className="results">
      <p className="hint">
        An <b>eval run</b> sends every labelled event in a test set to a classifier and compares its verdict with the
        label. Pick a test set to compare the classifiers on it, then click a row to see exactly where that run went
        wrong.
      </p>

      <div className="row compact">
        {splits.map((s) => (
          <button key={s} className={s === split ? "tab active" : "tab"} onClick={() => setSplit(s)}>
            {SPLITS[s]?.title ?? s}
          </button>
        ))}
      </div>

      {info && (
        <div className="split-info">
          <p>
            <b>
              {info.title}: {nCases} events.
            </b>{" "}
            {info.about}
          </p>
          <p className="small">{info.comparable}</p>
        </div>
      )}

      <RunTable runs={shown} selected={current?.run_id ?? ""} onSelect={setSelected} />
      {(hidden > 0 || includeOlder) && (
        <label className="inline small">
          <input type="checkbox" checked={includeOlder} onChange={(e) => setIncludeOlder(e.target.checked)} />
          &nbsp;also show older reruns of the same classifier and setting
          {hidden > 0 && ` (${hidden} hidden)`}
        </label>
      )}
      <HowToRead split={split} />

      {current && <RunDetails key={current.run_id} summary={current} caseById={caseById} />}
    </section>
  );
}
