// Hand-mirrored from the domain models (src/pac/guardrail/domain, src/pac/evaluation/domain)
// and src/pac/interfaces/api.py -- only what the UI reads.

export type EventKind = "user_input" | "model_output" | "tool_call" | "tool_response";
export type Verdict = "allow" | "violation" | "policy_silent";

export const EVENT_KINDS: EventKind[] = ["user_input", "model_output", "tool_call", "tool_response"];
export const VERDICTS: Verdict[] = ["allow", "violation", "policy_silent"];

export interface TraceEvent {
  kind: EventKind;
  content?: string | null;
  tool_name?: string | null;
  arguments?: Record<string, unknown> | null;
}

export interface Session {
  verified: boolean;
  verified_customer_id: string | null;
  email_on_file: string | null;
}

export interface ClassificationRequest {
  event: TraceEvent;
  session: Session;
  history?: TraceEvent[];
}

export interface Decision {
  rationale: string;
  verdict: Verdict;
  rule_ids: string[];
  confidence: number;
}

export interface Usage {
  input_tokens: number;
  output_tokens: number;
  cache_read_input_tokens: number;
}

export interface Classification {
  decision: Decision;
  model: string;
  prompt_version: string;
  policy_version: string;
  latency_ms: number;
  model_latency_ms: number;
  usage: Usage;
  cached: boolean;
  error: string | null;
}

export interface LabeledCase {
  id: string;
  split: "dev" | "test";
  request: ClassificationRequest;
  label: Verdict;
  rule_ids: string[];
  label_confidence: "high" | "medium" | "contested";
  rationale: string;
  tags: string[];
  origin: string;
}

export interface Health {
  backend: string;
  model: string;
  llm_available: boolean;
  llm_error: string | null;
}

export interface Rate {
  k: number;
  n: number;
  value: number | null;
  ci_low: number | null;
  ci_high: number | null;
}

export interface SliceScores {
  n: number;
  accuracy: Rate;
  violation_recall: Rate;
  false_block_rate: Rate;
}

export interface Metrics {
  n_cases: number;
  accuracy: Rate;
  macro_f1: number | null;
  macro_f1_ci: [number, number] | null;
  confusion: Record<Verdict, Record<Verdict | "error", number>>;
  violation_recall: Rate;
  violation_caught_or_escalated: Rate;
  violation_precision: Rate;
  false_block_rate: Rate;
  escalation_rate_on_allow: Rate;
  silent_recall: Rate;
  rule_attribution: Rate;
  by_kind: Record<string, SliceScores>;
  by_label_confidence: Record<string, SliceScores>;
  expected_calibration_error: number | null;
  operational: {
    model_latency_p50_ms: number | null;
    model_latency_p95_ms: number | null;
    mean_input_tokens: number;
    mean_output_tokens: number;
    cost_per_1k_events_usd: number | null;
    classifier_errors: Rate;
  };
}

export interface RunSummary {
  run_id: string;
  created_at: string;
  classifier: string;
  backend: string;
  prompt_version: string;
  split: string;
  n_cases: number;
  with_history: boolean;
  accuracy: number | null;
  violation_recall: number | null;
  false_block_rate: number | null;
}

export interface Prediction {
  case_id: string;
  repeat: number;
  kind: EventKind;
  gold: Verdict;
  gold_rules: string[];
  label_confidence: string;
  tags: string[];
  result: Classification;
}

export interface RunDetail {
  config: {
    run_id: string;
    classifier: string;
    backend: string;
    prompt_version: string;
    policy_version: string;
    split: string;
    with_history: boolean;
    git_commit: string | null;
  };
  metrics: Metrics;
  predictions: Prediction[];
}
