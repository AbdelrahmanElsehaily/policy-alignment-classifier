import { createContext, useContext } from "react";

import { nameRules, type RuleBook, ruleLabel } from "../rules";

/** The rules of the policy the classifier judges by (edited or not), keyed by id. */
export const RulesContext = createContext<RuleBook>(new Map());

/** Rule ids as named chips; hovering one shows the rule's text and the events it applies to. */
export function RuleList({ ids }: { ids: readonly string[] }) {
  const book = useContext(RulesContext);
  if (ids.length === 0) return null;
  return (
    <span className="rule-list">
      {ids.map((id) => {
        const rule = book.get(id);
        return (
          <span
            key={id}
            className="rule-chip"
            title={rule ? `${rule.title}. ${rule.text} (Applies to: ${rule.appliesTo.join(", ")}.)` : id}
          >
            {ruleLabel(id, book)}
          </span>
        );
      })}
    </span>
  );
}

/** Text that cites rules by id, with each rule named the first time. */
export function useRuleNames(): (text: string | null | undefined) => string {
  const book = useContext(RulesContext);
  return (text) => (text ? nameRules(text, book) : "");
}
