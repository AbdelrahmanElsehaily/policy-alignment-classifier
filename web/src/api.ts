import { readNdjson } from "./ndjson";
import type {
  Classification,
  ClassificationRequest,
  ConversationSetup,
  DemoExample,
  Engine,
  Health,
  LabeledCase,
  RunDetail,
  RunSummary,
  TurnRequest,
  TurnUpdate,
} from "./types";

async function failure(response: Response): Promise<Error> {
  let detail = response.statusText;
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (body.detail) detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
  } catch {
    // keep statusText
  }
  return new Error(`${response.status}: ${detail}`);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!response.ok) throw await failure(response);
  return (await response.json()) as T;
}

export type { Engine };

/** Must match API_VERSION in src/pac/interfaces/api.py. */
export const API_VERSION = 2;

/** A server started before the last API change, still running, serving this newer page. */
export function isStaleServer(health: Health): boolean {
  return health.api_version !== API_VERSION;
}

export const api = {
  health: () => request<Health>("/api/health"),
  cases: () => request<LabeledCase[]>("/api/cases"),
  examples: () => request<DemoExample[]>("/api/examples"),
  runs: () => request<RunSummary[]>("/api/runs"),
  run: (id: string) => request<RunDetail>(`/api/runs/${encodeURIComponent(id)}`),
  classify: (body: ClassificationRequest, engine: Engine = "llm") =>
    request<Classification>(`/api/classify?engine=${engine}`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  conversation: () => request<ConversationSetup>("/api/conversation"),
  /** One live turn; ``onUpdate`` sees each event and verdict as the server streams it. */
  turn: async (body: TurnRequest, onUpdate: (update: TurnUpdate) => void, signal?: AbortSignal) => {
    const response = await fetch("/api/conversation/turn", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal,
    });
    if (!response.ok) throw await failure(response);
    if (!response.body) throw new Error("The server sent no stream");
    await readNdjson<TurnUpdate>(response.body, onUpdate);
  },
};
