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
  split: "dev" | "test" | "hard" | "stress" | "agents";
  request: ClassificationRequest;
  label: Verdict;
  rule_ids: string[];
  label_with_history: Verdict | null;
  rule_ids_with_history: string[];
  label_confidence: "high" | "medium" | "contested";
  rationale: string;
  tags: string[];
  origin: string;
}

export interface Health {
  /** Missing on servers older than the version check itself. */
  api_version?: number;
  backend: string;
  model: string;
  live: boolean;
  llm_available: boolean;
  llm_error: string | null;
}

export interface DemoExample {
  title: string;
  look_for: string;
  case: LabeledCase;
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
  n_templates?: number | null;
  accuracy_template_ci?: [number, number] | null;
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
  dataset: string;
  n_cases: number;
  repeats: number;
  with_history: boolean;
  accuracy: Rate;
  accuracy_template_ci: [number, number] | null;
  violation_recall: Rate;
  false_block_rate: Rate;
  silent_recall: Rate;
  classifier_errors: Rate;
  latency_p50_ms: number | null;
  /** Accuracy of each run when the set was run more than once; the other columns are run 1. */
  accuracy_by_run: number[];
  /** Events longer than this were judged in parts. */
  max_event_chars: number | null;
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
    created_at: string;
    classifier: string;
    backend: string;
    prompt_version: string;
    policy_version: string;
    split: string;
    dataset: string;
    dataset_sha256: string;
    repeats: number;
    with_history: boolean;
    git_commit: string | null;
  };
  metrics: Metrics;
  predictions: Prediction[];
}

// Live conversation (src/pac/interfaces/live_conversation.py, src/pac/simulation)

export interface CustomerView {
  customer_id: string;
  name: string;
  email: string;
}

export interface Suggestion {
  label: string;
  message: string;
  verify_as: string | null;
  exercises: string;
  /** Missing on older servers, which then show every scenario. */
  featured?: boolean;
}

export interface ConversationSetup {
  available: boolean;
  error: string | null;
  agent_model: string | null;
  customers: CustomerView[];
  suggestions: Suggestion[];
}

/** What the inline guardrail did with an event (src/pac/guardrail/domain/enforcement.py). */
export type Gate = "pass" | "block" | "hold";

export interface HistoryEntry {
  event: TraceEvent;
  gate: Gate;
  by_guardrail: boolean;
}

export interface TurnRequest {
  session: Session;
  history: HistoryEntry[];
  message: string;
  guardrail_sees_history: boolean;
}

export type Engine = "llm" | "baseline";

export type TurnUpdate =
  | { type: "event"; index: number; event: TraceEvent; by_guardrail: boolean }
  | { type: "verdict"; index: number; engine: Engine; classification: Classification }
  | { type: "gate"; index: number; gate: Gate }
  | { type: "stopped"; reason: string }
  | { type: "done" };

// Drift monitoring (src/pac/monitoring)

export type DriftStatus = "stable" | "moderate" | "major" | "insufficient" | "not_compared";

export interface FeatureDrift {
  feature: string;
  group: "input" | "output";
  psi: number | null;
  status: DriftStatus;
  movers: [string, number, number][];
  new: string[];
}

export interface DriftReport {
  reference_events: number;
  current_events: number;
  status: DriftStatus;
  features: FeatureDrift[];
}

export interface MonitoringSnapshot {
  totals: {
    events: number;
    by_verdict: Record<string, number>;
    by_kind: Record<string, number>;
    errors: number;
    latency_ms_sum: number;
  };
  window_events: number;
  window_size: number;
  has_reference: boolean;
  drift: DriftReport | null;
}

export interface PolicyView {
  version: string;
  sha256: string;
  rule_ids: string[];
  text: string;
  /** The classifier judges by an edited policy, not the policy file. */
  edited: boolean;
  /** Why it can't be edited here (replay mode, no model), or null. */
  locked: string | null;
}
