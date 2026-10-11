import { CORNER_CASES } from "../cornerCases";

export function CornerCases() {
  return (
    <section>
      <p className="hint">
        Where the policy does not decide. Each case says what happens, why the policy is silent, what I would ask
        Larkspur, and what the guardrail can do in the meantime. The full list is in{" "}
        <code>docs/CORNER_CASES.md</code> in the repository.
      </p>
      <div className="examples">
        {CORNER_CASES.map((c) => (
          <div key={c.id} className="card example">
            <div className="card-head">
              <strong>
                {c.id}. {c.title}
              </strong>
              <span className="small">{c.kind}</span>
            </div>
            <p className="small">{c.what}</p>
            <p>
              <b>Why the policy is silent.</b> {c.silent}
            </p>
            <p>
              <b>What I'd ask Larkspur.</b> {c.ask}
            </p>
            <p>
              <b>What I'd ship meanwhile.</b> {c.ship}
            </p>
            <p className="small">Eval cases: {c.cases.map((id) => <code key={id}>{id} </code>)}</p>
          </div>
        ))}
      </div>
    </section>
  );
}
