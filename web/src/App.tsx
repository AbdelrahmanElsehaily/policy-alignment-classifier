import { useEffect, useState } from "react";

import { api } from "./api";
import { ClassifyPanel } from "./components/ClassifyPanel";
import { ConversationPanel } from "./components/ConversationPanel";
import { ExamplesPanel } from "./components/ExamplesPanel";
import { PositionDemo } from "./components/PositionDemo";
import { ResultsPanel } from "./components/ResultsPanel";
import type { Health, LabeledCase } from "./types";

const TABS = ["Examples", "Live conversation", "Classify an event", "Position demo", "Eval results"] as const;
type Tab = (typeof TABS)[number];

export function App() {
  const [tab, setTab] = useState<Tab>("Examples");
  const [health, setHealth] = useState<Health | null>(null);
  const [cases, setCases] = useState<LabeledCase[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.health().then(setHealth).catch((e: Error) => setError(e.message));
    api.cases().then(setCases).catch((e: Error) => setError(e.message));
  }, []);

  return (
    <main>
      <header>
        <h1>Policy alignment classifier</h1>
        <p className="hint">
          Lark, the support agent for Larkspur Electronics (fictional), with policy v0.{" "}
          {health &&
            (health.llm_available ? (
              <span className={health.live ? "mode live" : "mode recorded"}>
                {health.live ? "LIVE" : "RECORDED"} · <code>{health.model}</code> via <code>{health.backend}</code>
                {health.live ? " · every request calls the model" : " · answers replayed from the cache"}
              </span>
            ) : (
              <span className="error">LLM unavailable: {health.llm_error} (the regex baseline still works)</span>
            ))}
        </p>
        <nav>
          {TABS.map((t) => (
            <button key={t} className={t === tab ? "tab active" : "tab"} onClick={() => setTab(t)}>
              {t}
            </button>
          ))}
        </nav>
      </header>
      {error && <p className="error">{error}</p>}
      {tab === "Examples" && <ExamplesPanel health={health} />}
      {tab === "Live conversation" && <ConversationPanel />}
      {tab === "Classify an event" && <ClassifyPanel cases={cases} />}
      {tab === "Position demo" && <PositionDemo />}
      {tab === "Eval results" && <ResultsPanel cases={cases} />}
    </main>
  );
}
