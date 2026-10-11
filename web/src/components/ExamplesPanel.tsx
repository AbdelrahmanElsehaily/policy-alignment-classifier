import { useEffect, useState } from "react";

import { api } from "../api";
import { describeEvent, ms } from "../format";
import type { Classification, DemoExample, Health, Verdict } from "../types";
import { VerdictBadge } from "./VerdictCard";

interface Outcome {
  llm?: Classification;
  baseline?: Classification;
  error?: string;
  withHistory: boolean;
}


function expected(example: DemoExample, withHistory: boolean): { verdict: Verdict; rules: string[] } {
  const c = example.case;
  if (withHistory && c.label_with_history) return { verdict: c.label_with_history, rules: c.rule_ids_with_history };
  return { verdict: c.label, rules: c.rule_ids };
}

export function ExamplesPanel({ health }: { health: Health | null }) {
  const [examples, setExamples] = useState<DemoExample[]>([]);
  const [outcomes, setOutcomes] = useState<Record<string, Outcome>>({});
  const [running, setRunning] = useState<string | null>(null);
  const [runAll, setRunAll] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .examples()
      .then(setExamples)
      .catch((e: Error) => setError(e.message));
  }, []);

  async function run(example: DemoExample, withHistory: boolean) {
    const key = example.case.id;
    setRunning(key);
    const { event, session, history } = example.case.request;
    const body = { event, session, history: withHistory ? history : [] };
    const [llm, baseline] = await Promise.allSettled([api.classify(body, "llm"), api.classify(body, "baseline")]);
    setOutcomes((prev) => ({
      ...prev,
      [key]: {
        withHistory,
        llm: llm.status === "fulfilled" ? llm.value : undefined,
        baseline: baseline.status === "fulfilled" ? baseline.value : undefined,
        error: llm.status === "rejected" ? (llm.reason as Error).message : undefined,
      },
    }));
    setRunning(null);
  }

  async function runEverything() {
    setRunAll(true);
    for (const example of examples) {
      await run(example, false); // one at a time: a local model serves one request well
    }
    setRunAll(false);
  }

  const live = health?.live ?? false;
  return (
    <section>
      <p className="hint">
        Curated scenarios from the eval set. Each card shows the expected verdict; <b>Run</b> asks the classifier{" "}
        {live ? (
          <>
            <b>live</b> ({health?.model}, every click is a real model call
            {health?.backend === "ollama" ? ", about 10 s each on a laptop GPU" : ""})
          </>
        ) : (
          <>using recorded answers (start with <code>--live</code> for real model calls)</>
        )}
        , next to the flat-text regex baseline.
      </p>
      <div className="row">
        <button onClick={runEverything} disabled={runAll || running !== null || examples.length === 0}>
          {runAll ? "Running all…" : `Run all ${examples.length}`}
        </button>
      </div>
      {error && <p className="error">{error}</p>}
      <div className="examples">
        {examples.map((example) => {
          const c = example.case;
          const outcome = outcomes[c.id];
          const want = expected(example, outcome?.withHistory ?? false);
          const got = outcome?.llm;
          const agrees = got && !got.error && got.decision.verdict === want.verdict;
          return (
            <div key={c.id} className="card example">
              <div className="card-head">
                <strong>{example.title}</strong>
                <span className="small">
                  <code>{c.request.event.kind}</code> · {c.request.session.verified ? "verified" : "unverified"}
                </span>
              </div>
              <pre className="event">{describeEvent(c.request.event)}</pre>
              <p className="small">{example.look_for}</p>
              <div className="row compact">
                <span className="small">expected</span> <VerdictBadge verdict={c.label} />{" "}
                <span className="small">{c.rule_ids.join(", ")}</span>
                {c.label_with_history && (
                  <>
                    <span className="small">· with history</span> <VerdictBadge verdict={c.label_with_history} />{" "}
                    <span className="small">{c.rule_ids_with_history.join(", ")}</span>
                  </>
                )}
              </div>
              <div className="row compact">
                <button onClick={() => run(example, false)} disabled={running !== null}>
                  {running === c.id ? "Calling the model…" : "Run"}
                </button>
                {c.request.history && c.request.history.length > 0 && (
                  <button className="secondary" onClick={() => run(example, true)} disabled={running !== null}>
                    Run with history ({c.request.history.length} earlier events)
                  </button>
                )}
              </div>
              {outcome && (
                <div className="result">
                  {outcome.error && <p className="error">{outcome.error}</p>}
                  {got && (
                    <>
                      <div className="row compact">
                        <span className="small">LLM{outcome.withHistory ? " + history" : ""}</span>
                        <VerdictBadge verdict={got.decision.verdict} error={got.error} />
                        <span className="small">{got.decision.rule_ids.join(", ")}</span>
                        <span className={agrees ? "match" : "mismatch"}>{agrees ? "✓ as expected" : "✗ differs"}</span>
                        <span className="small">
                          {got.cached ? "recorded" : "live"} · {ms(got.model_latency_ms)}
                        </span>
                      </div>
                      <p className="rationale small">{got.error ?? got.decision.rationale}</p>
                    </>
                  )}
                  {outcome.baseline && (
                    <div className="row compact">
                      <span className="small">regex baseline</span>
                      <VerdictBadge verdict={outcome.baseline.decision.verdict} />
                      <span className="small">{outcome.baseline.decision.rule_ids.join(", ")}</span>
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}
