import { CORNER_CASES } from "../cornerCases";

interface Props {
  /** Case ids the page can load; a case outside them gets no Try button. */
  known: ReadonlySet<string>;
  onTry: (caseId: string) => void;
}

export function CornerCases({ known, onTry }: Props) {
  return (
    <section>
      <p className="hint">
        Events a real deployment will meet that the policy does not decide: it is silent, or its own rules conflict.
        Each says what happens, why the policy is silent, what I'd ask Larkspur, and what the guardrail can do by
        itself in the meantime. Click a case id to open it in Try it.
      </p>
      <p className="small">
        <code>docs/CORNER_CASES.md</code> has six shorter corner cases besides these, and, separately, findings where
        the policy does decide the event: events that lack the facts a rule needs, attacks only the history reveals,
        and side effects of blocking.
      </p>
      <div className="corner-list">
        {CORNER_CASES.map((c) => (
          <details key={c.id} className="corner">
            <summary>
              <span className="corner-title">
                {c.id}. {c.title}
              </span>
              <span className="small corner-kind">{c.kind}</span>
            </summary>
            <div className="corner-body">
              <p>
                <b>What happens.</b> {c.what}
              </p>
              <p>
                <b>Why the policy is silent.</b> {c.silent}
              </p>
              <p>
                <b>What I'd ask Larkspur.</b> {c.ask}
              </p>
              <p>
                <b>What I'd ship meanwhile.</b> {c.ship}
              </p>
              <div className="row compact">
                <span className="small">Eval cases:</span>
                {c.cases.map((id) =>
                  known.has(id) ? (
                    <button key={id} className="chip" onClick={() => onTry(id)} title="Open it in Try it">
                      {id} ▸
                    </button>
                  ) : (
                    <code key={id}>{id}</code>
                  ),
                )}
              </div>
            </div>
          </details>
        ))}
      </div>
    </section>
  );
}
