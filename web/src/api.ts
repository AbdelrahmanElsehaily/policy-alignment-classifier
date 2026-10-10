import type {
  Classification,
  ClassificationRequest,
  DemoExample,
  Health,
  LabeledCase,
  RunDetail,
  RunSummary,
} from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (body.detail) detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      // keep statusText
    }
    throw new Error(`${response.status}: ${detail}`);
  }
  return (await response.json()) as T;
}

export type Engine = "llm" | "baseline";

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
};
