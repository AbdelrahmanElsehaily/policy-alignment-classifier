import { ms, pct } from "../format";
import type { Classification, Verdict } from "../types";

export function VerdictBadge({ verdict, error }: { verdict: Verdict; error?: string | null }) {
  if (error) {
    return (
      <span className="badge badge-error" title={error}>
        error → human review
      </span>
    );
  }
  const label = verdict === "policy_silent" ? "policy silent" : verdict;
  return <span className={`badge badge-${verdict}`}>{label}</span>;
}

export function VerdictCard({ title, result }: { title: string; result: Classification }) {
  const { decision } = result;
  return (
    <div className="card">
      <div className="card-head">
        <strong>{title}</strong>
        <VerdictBadge verdict={decision.verdict} error={result.error} />
      </div>
      {decision.rule_ids.length > 0 && <div className="rules">Rules: {decision.rule_ids.join(", ")}</div>}
      <p className="rationale">{decision.rationale}</p>
      {result.error && <p className="error">Fail-closed: {result.error}</p>}
      <div className="meta">
        confidence {pct(decision.confidence)} · model {ms(result.model_latency_ms)}
        {result.cached ? " (recorded)" : ""} · {result.usage.input_tokens} in / {result.usage.output_tokens} out ·{" "}
        {result.model}
      </div>
    </div>
  );
}
