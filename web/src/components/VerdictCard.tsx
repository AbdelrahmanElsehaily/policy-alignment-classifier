import { ms, pct } from "../format";
import type { Classification, Verdict } from "../types";
import { RuleList, useRuleNames } from "./Rules";

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
  const named = useRuleNames();
  return (
    <div className="card">
      <div className="card-head">
        <strong>{title}</strong>
        <VerdictBadge verdict={decision.verdict} error={result.error} />
      </div>
      {decision.rule_ids.length > 0 && (
        <div className="rules">
          Rules broken: <RuleList ids={decision.rule_ids} />
        </div>
      )}
      <p className="rationale">{named(decision.rationale)}</p>
      {result.error && <p className="error">Fail-closed: {result.error}</p>}
      <div className="meta">
        {result.model === "regex-baseline" ? "regex" : result.cached ? "recorded answer" : "live call"} · model{" "}
        {ms(result.model_latency_ms)} · confidence {pct(decision.confidence)} · {result.usage.input_tokens} in /{" "}
        {result.usage.output_tokens} out · {result.model}
      </div>
    </div>
  );
}
