// The policy's rules by id, so the UI can say "R3 · Payment data" instead of a bare "R3".

import type { EventKind } from "./types";

export interface PolicyRule {
  id: string;
  title: string;
  text: string;
  appliesTo: EventKind[];
}

export type RuleBook = ReadonlyMap<string, PolicyRule>;

/** The numbered rules in the policy text: "**R5 — Refunds.** … *Applies to: tool_call.*" */
export function parseRules(policy: string): PolicyRule[] {
  const rules: PolicyRule[] = [];
  for (const block of policy.split(/\n\s*\n/)) {
    const m = /^\*\*(R\d+)\s+—\s+(.+?)\.\*\*\s*([\s\S]*)$/.exec(block.trim());
    if (!m) continue;
    const [, id = "", title = "", rest = ""] = m;
    const applies = /\*Applies to:\s*([^*]+?)\.?\*/.exec(rest);
    rules.push({
      id,
      title,
      text: rest.replace(/\*Applies to:[^*]*\*/, "").replace(/[*`]/g, "").replace(/\s+/g, " ").trim(),
      appliesTo: applies?.[1] ? (applies[1].split(",").map((s) => s.trim()) as EventKind[]) : [],
    });
  }
  return rules;
}

export function ruleBook(policy: string): RuleBook {
  return new Map(parseRules(policy).map((r) => [r.id, r]));
}

/** "R3 · Payment data", or the bare id for a rule the policy doesn't have. */
export function ruleLabel(id: string, book: RuleBook): string {
  const rule = book.get(id);
  return rule ? `${id} · ${rule.title}` : id;
}

/** Names each rule the first time prose mentions it: "under R13" -> "under R13 (Manipulation by customers)". */
export function nameRules(text: string, book: RuleBook): string {
  const seen = new Set<string>();
  return text.replace(/\bR(\d{1,2})\b(?!\s*\()/g, (id) => {
    const rule = book.get(id);
    if (!rule || seen.has(id)) return id;
    seen.add(id);
    return `${id} (${rule.title})`;
  });
}
