import { useEffect, useState } from "react";

import { api } from "../api";
import type { PolicyView } from "../types";
import { PolicyText } from "./PolicyText";

/** The policy the classifier judges by. Editing it (with a model running) changes the verdicts. */
export function PolicyPanel({ onChange }: { onChange?: (policy: PolicyView) => void }) {
  const [policy, setPolicy] = useState<PolicyView | null>(null);
  const [draft, setDraft] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function show(next: PolicyView) {
    setPolicy(next);
    onChange?.(next);
  }

  useEffect(() => {
    api.policy().then(show).catch((e: Error) => setError(e.message));
  }, []);

  async function apply(action: () => Promise<PolicyView>) {
    setBusy(true);
    setError(null);
    try {
      show(await action());
      setDraft(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (!policy) return <p className="small pending">{error ?? "Loading the policy…"}</p>;
  const editing = draft !== null;
  const changed = editing && draft !== policy.text;

  return (
    <section className="policy-panel">
      <div className="policy-head">
        <div>
          <strong>Policy {policy.version}</strong>{" "}
          {policy.edited ? (
            <span className="badge badge-policy_silent">edited in the app</span>
          ) : (
            <span className="small">
              as in <code>policy/policy_v0.md</code>
            </span>
          )}{" "}
          <span className="small">
            · {policy.rule_ids.length} rules · <code>{policy.sha256.slice(0, 12)}</code>
          </span>
        </div>
        <div className="row compact">
          {!editing && (
            <button disabled={!!policy.locked} onClick={() => setDraft(policy.text)}>
              Edit
            </button>
          )}
          {editing && (
            <>
              <button disabled={busy || !changed} onClick={() => void apply(() => api.editPolicy(draft))}>
                {busy ? "Applying…" : "Apply to the classifier"}
              </button>
              <button className="secondary" disabled={busy} onClick={() => setDraft(null)}>
                Cancel
              </button>
            </>
          )}
          {policy.edited && !editing && (
            <button className="secondary" disabled={busy} onClick={() => void apply(api.resetPolicy)}>
              Reset to the file
            </button>
          )}
        </div>
      </div>
      <p className="hint">
        The classifier gets this text in its prompt. An edit applies at once to <em>Try one event</em> and the{" "}
        <em>Live conversation</em>, until you reset it or restart the server; the file isn't changed, and the eval
        results stay those of the file's policy. The agent never sees this text, and the regex baseline doesn't
        read it.
      </p>
      {policy.locked && <p className="policy-locked">{policy.locked}</p>}
      {error && <p className="error">{error}</p>}
      {editing ? (
        <textarea
          className="policy-editor"
          value={draft}
          spellCheck={false}
          onChange={(e) => setDraft(e.target.value)}
        />
      ) : (
        <div className="card policy-body">
          <PolicyText text={policy.text} />
        </div>
      )}
    </section>
  );
}
