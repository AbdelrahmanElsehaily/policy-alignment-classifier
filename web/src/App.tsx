import { type JSX, useEffect, useMemo, useState } from "react";

import { api, isStalePage, isStaleServer } from "./api";
import { ConversationPanel } from "./components/ConversationPanel";
import { CornerCases } from "./components/CornerCases";
import { MonitoringPanel } from "./components/MonitoringPanel";
import { PolicyPanel } from "./components/PolicyPanel";
import { RulesContext } from "./components/Rules";
import { ResultsPanel } from "./components/ResultsPanel";
import { TryItPanel } from "./components/TryItPanel";
import { ruleBook } from "./rules";
import type { Health, LabeledCase, PolicyView } from "./types";

const TABS = ["Policy", "Try one event", "Live conversation", "Findings", "Eval results", "Monitoring"] as const;
type Tab = (typeof TABS)[number];

export function App() {
  const [tab, setTab] = useState<Tab>("Policy");
  // Panels stay mounted once opened and are only hidden when inactive, so switching tabs keeps
  // their state: a conversation, example results, a half-edited event, a selected run.
  const [opened, setOpened] = useState<ReadonlySet<Tab>>(() => new Set<Tab>(["Policy"]));
  const [health, setHealth] = useState<Health | null>(null);
  const [cases, setCases] = useState<LabeledCase[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [policy, setPolicy] = useState<PolicyView | null>(null);
  // A case another tab asked Try one event to open; the counter makes a repeat request count.
  const [toTry, setToTry] = useState<{ id: string; n: number } | null>(null);
  const known = useMemo(() => new Set(cases.map((c) => c.id)), [cases]);
  // The rules of the policy the classifier judges by, so every panel can name them.
  const rules = useMemo(() => ruleBook(policy?.text ?? ""), [policy]);

  useEffect(() => {
    api.health().then(setHealth).catch((e: Error) => setError(e.message));
    api.cases().then(setCases).catch((e: Error) => setError(e.message));
    api.policy().then(setPolicy).catch((e: Error) => setError(e.message));
  }, []);

  function open(next: Tab) {
    setTab(next);
    setOpened((prev) => (prev.has(next) ? prev : new Set(prev).add(next)));
  }

  function tryCase(id: string) {
    setToTry((prev) => ({ id, n: (prev?.n ?? 0) + 1 }));
    open("Try one event");
  }

  const panels: Record<Tab, JSX.Element> = {
    "Try one event": <TryItPanel cases={cases} health={health} request={toTry} />,
    "Live conversation": <ConversationPanel />,
    Monitoring: <MonitoringPanel />,
    Policy: <PolicyPanel onChange={setPolicy} />,
    Findings: <CornerCases known={known} onTry={tryCase} />,
    "Eval results": <ResultsPanel cases={cases} />,
  };

  return (
    <main>
      <header>
        <h1>Policy alignment classifier</h1>
        <p className="hint">
          Lark, the support agent for Larkspur Electronics (fictional), with policy {policy?.version ?? "v0"}
          {policy?.edited && (
            <>
              {" "}
              <button className="link" onClick={() => open("Policy")}>
                (edited)
              </button>
            </>
          )}
          .{" "}
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
      <RulesContext.Provider value={rules}>
        {TABS.filter((t) => opened.has(t)).map((t) => (
          <div key={t} hidden={t !== tab}>
            {panels[t]}
          </div>
        ))}
      </RulesContext.Provider>
    </main>
  );
}
