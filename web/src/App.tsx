import { type JSX, useEffect, useState } from "react";

import { api, isStalePage, isStaleServer } from "./api";
import { ClassifyPanel } from "./components/ClassifyPanel";
import { ConversationPanel } from "./components/ConversationPanel";
import { CornerCases } from "./components/CornerCases";
import { ExamplesPanel } from "./components/ExamplesPanel";
import { MonitoringPanel } from "./components/MonitoringPanel";
import { PositionDemo } from "./components/PositionDemo";
import { ResultsPanel } from "./components/ResultsPanel";
import type { Health, LabeledCase } from "./types";

const TABS = ["Try it", "Live conversation", "Results & findings"] as const;
type Tab = (typeof TABS)[number];

export function App() {
  const [tab, setTab] = useState<Tab>("Try it");
  // Panels stay mounted once opened and are only hidden when inactive, so switching tabs keeps
  // their state: a conversation, example results, a half-edited event, a selected run.
  const [opened, setOpened] = useState<ReadonlySet<Tab>>(() => new Set<Tab>(["Try it"]));
  const [health, setHealth] = useState<Health | null>(null);
  const [cases, setCases] = useState<LabeledCase[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.health().then(setHealth).catch((e: Error) => setError(e.message));
    api.cases().then(setCases).catch((e: Error) => setError(e.message));
  }, []);

  function open(next: Tab) {
    setTab(next);
    setOpened((prev) => (prev.has(next) ? prev : new Set(prev).add(next)));
  }

  const panels: Record<Tab, JSX.Element> = {
    "Try it": (
      <>
        <h2>Curated examples</h2>
        <ExamplesPanel health={health} />
        <h2>Classify your own event</h2>
        <ClassifyPanel cases={cases} />
        <h2>The same text in each position</h2>
        <PositionDemo />
      </>
    ),
    "Live conversation": <ConversationPanel />,
    "Results & findings": (
      <>
        <h2>Corner cases: where the policy does not decide</h2>
        <CornerCases />
        <h2>Eval results</h2>
        <ResultsPanel cases={cases} />
        <details className="section-fold">
          <summary>
            <h2>Monitoring (production drift)</h2>
          </summary>
          <MonitoringPanel />
        </details>
      </>
    ),
  };

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
            <button key={t} className={t === tab ? "tab active" : "tab"} onClick={() => open(t)}>
              {t}
            </button>
          ))}
        </nav>
      </header>
      {health && isStaleServer(health) && (
        <p className="error">
          The server is older than this page (API version {health.api_version ?? 1}, page expects a newer one), so
          some requests will fail. Restart <code>pac serve</code>.
        </p>
      )}
      {health && isStalePage(health) && (
        <p className="error">
          This page is older than the server (it was loaded before the server was updated), so some requests will
          fail. Reload the page.
        </p>
      )}
      {error && <p className="error">{error}</p>}
      {TABS.filter((t) => opened.has(t)).map((t) => (
        <div key={t} hidden={t !== tab}>
          {panels[t]}
        </div>
      ))}
    </main>
  );
}
