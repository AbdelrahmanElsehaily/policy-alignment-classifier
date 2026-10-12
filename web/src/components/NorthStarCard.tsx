import { useEffect, useState } from "react";

import { api } from "../api";
import { percent, runModel, SHIPPED_PROMPT, SPLITS } from "../results";

const exact = (v: number) => `${(v * 100).toFixed(1)}%`;
import type { NorthStar } from "../types";

function prompt(s: NorthStar): string {
  return s.classifier === "regex-baseline" ? "" : s.prompt_version.split("@")[0]!.replace("classifier_", "prompt ");
}

/** The one number: of every held-out event, the share handled as labelled. */
export function NorthStarCard() {
  const [stars, setStars] = useState<NorthStar[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.northStar().then(setStars).catch((e: Error) => setError(e.message));
  }, []);
  if (error) return <p className="error">{error}</p>;
  if (!stars) return null;

  const current = stars.filter((s) => s.classifier === "regex-baseline" || s.prompt_version.startsWith(SHIPPED_PROMPT));
  const complete = current.filter((s) => s.missing.length === 0);
  const partial = current.filter((s) => s.missing.length > 0);
  const shipped = complete.find((s) => s.classifier === "MiniMax-M3") ?? complete[0];
  if (!shipped) return null;
  const names = (sets: string[]) => sets.map((s) => SPLITS[s]?.title ?? s).join(", ");

  return (
    <div className="north-star">
      <div className="north-star-head">
        <div>
          <p className="eyebrow">North star · {shipped.total} held-out events</p>
          <p className="north-star-number">{exact(shipped.accuracy)}</p>
          <p className="small">
            handled correctly by the shipped classifier ({runModel(shipped)}, {prompt(shipped)}):{" "}
            {shipped.correct} of {shipped.total}
            {shipped.high > shipped.low && `, ${percent(shipped.low)}–${percent(shipped.high)} over repeated runs`}
          </p>
        </div>
        <table className="north-star-table">
          <thead>
            <tr>
              <th>Classifier</th>
              <th title="Held-out events handled correctly">North star</th>
              <th title="At list price, without prompt caching">$ per 1,000 events</th>
              <th>Median latency</th>
            </tr>
          </thead>
          <tbody>
            {complete.map((s) => (
              <tr key={s.classifier + s.prompt_version} className={s === shipped ? "shipped-row" : undefined}>
                <td>
                  {runModel(s)} <span className="small">{prompt(s)}</span>
                </td>
                <td>
                  <strong>{exact(s.accuracy)}</strong>{" "}
                  <span className="small">
                    {s.correct}/{s.total}
                  </span>
                </td>
                <td>{s.usd_per_1k_events == null ? "–" : `$${s.usd_per_1k_events.toFixed(2)}`}</td>
                <td>{s.latency_p50_ms == null ? "instant" : `${(s.latency_p50_ms / 1000).toFixed(1)} s`}</td>
              </tr>
            ))}
            {partial.map((s) => (
              <tr key={s.classifier + s.prompt_version} className="small">
                <td>{runModel(s)}</td>
                <td colSpan={3}>not yet run on {names(s.missing)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="small">
        Every event the prompt was neither written nor trained on (Test, Stress, Other agent, Agent pushed to use
        tools), pooled, one event one vote: pass what's harmless, block what breaks a rule, hold what the policy doesn't
        decide. A failed call counts as wrong. The 95 events prompt v3 was trained on are left out for every
        classifier, so all are scored on the same events. Cost is measured tokens at list price without prompt
        caching; caching the policy (most of every prompt) cuts the Claude input cost by about 90%.
      </p>
    </div>
  );
}
