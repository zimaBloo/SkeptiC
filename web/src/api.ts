// Types and calls for the SkeptiC API (src/skeptic/server.py).
// The record types mirror `uv run skeptic schema`; only the fields the UI uses.

export const CRITICS = ["market", "technical", "customer", "risk"] as const;
export type CriticName = (typeof CRITICS)[number];
export type Severity = "minor" | "major" | "fatal";
export type Overall = "strong" | "promising" | "weak" | "not viable";

export interface IdeaSummary {
  id: string;
  title: string;
  chars: number;
  sample: boolean;
}

export interface Idea {
  id: string;
  title: string;
  text: string;
}

export type RunStatus = "running" | "success" | "error" | "stopped";

export interface RunSummary {
  id: string;
  idea_title: string;
  status: RunStatus;
  started_at: string | null;
  finished_at: string | null;
}

export type ProgressEvent =
  | { time: string; event: "run_started"; critics: CriticName[]; models: Record<string, string> }
  | { time: string; event: "critic_started"; critic: CriticName; model: string }
  | {
      time: string;
      event: "critic_finished";
      critic: CriticName;
      ok: boolean;
      issues: number | null;
      overall: Overall | null;
      error: string | null;
    }
  | { time: string; event: "combiner_started"; model: string }
  | { time: string; event: "repair_started" }
  | { time: string; event: "combiner_finished"; ok: boolean; points: number | null; error: string | null }
  | { time: string; event: "run_finished"; ok: boolean; error: string | null };

export interface Issue {
  quote: string;
  issue: string;
  why_it_matters: string;
  how_to_improve: string;
  fixable_within_idea: boolean;
  severity: Severity;
}

export interface CriticVerdict {
  issues: Issue[];
  summary: string;
  overall: Overall;
}

export interface ServedModel {
  digest: string | null;
  quantization: string | null;
  parameter_size: string | null;
  context_length: number | null;
}

export interface CallTiming {
  started_at: string | null;
  finished_at: string | null;
  seconds: number | null;
}

export interface VerdictChecks {
  quotes_not_in_idea: string[];
  cut_off_fields: string[];
  severity_as_issue: string[];
  overall_mismatch: boolean;
  fatal_but_fixable: string[];
}

export interface CriticRecord {
  critic: CriticName;
  title: string;
  model: string;
  served: ServedModel | null;
  timing: CallTiming;
  verdict: CriticVerdict | null;
  model_overall: Overall | null;
  capped: number[];
  checks: VerdictChecks | null;
  error: string | null;
}

export interface IssueRef {
  id: string;
  critic: CriticName;
  index: number;
  severity: Severity;
  capped_from: Severity | null;
}

export interface PanelPoint {
  point: string;
  issues: IssueRef[];
  critics: CriticName[];
  severity: Severity;
  severity_split: boolean;
}

export interface Disagreement {
  point: string;
  positions: { critic: CriticName; claim: string }[];
}

export interface CombinedVerdict {
  points: PanelPoint[];
  disagreements: Disagreement[];
  improvements: string[];
  statement: string;
  unknown_issue_ids: string[];
  statement_repaired: boolean;
  first_statement: string | null;
}

export interface CombinerRecord {
  model: string | null;
  served: ServedModel | null;
  timing: CallTiming;
  combined: CombinedVerdict | null;
  error: string | null;
  repair_error?: string | null;
}

export interface PanelRecord {
  format_version: number;
  idea_id: string;
  idea_file: string | null;
  idea: string;
  status: string;
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
  log_file: string;
  git_commit: string | null;
  git_dirty: boolean | null;
  prompts_sha256: string | null;
  inspect_version: string | null;
  ollama_version: string | null;
  settings: Record<string, unknown>;
  critics: CriticRecord[];
  combiner: CombinerRecord;
  scores: Record<string, number>;
}

export interface RunDetail extends RunSummary {
  idea_text: string;
  events: ProgressEvent[];
  record: PanelRecord | null;
  error: string | null;
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === "string") message = body.detail;
      // FastAPI's validation errors: a list of {loc, msg}.
      else if (Array.isArray(body.detail)) {
        message = (body.detail as { loc?: unknown[]; msg?: string }[])
          .map((d) => [d.loc?.at(-1), d.msg].filter(Boolean).join(": "))
          .join("; ");
      }
    } catch {
      // keep the status line
    }
    throw new ApiError(message, response.status);
  }
  return (await response.json()) as T;
}

export const api = {
  ideas: () => request<IdeaSummary[]>("/ideas"),
  idea: (id: string) => request<Idea>(`/ideas/${encodeURIComponent(id)}`),
  runs: () => request<RunSummary[]>("/runs"),
  run: (id: string) => request<RunDetail>(`/runs/${encodeURIComponent(id)}`),
  startRun: (body: { idea_id: string } | { title: string; text: string }) =>
    request<{ id: string }>("/runs", { method: "POST", body: JSON.stringify(body) }),
  stopRun: (id: string) =>
    request<unknown>(`/runs/${encodeURIComponent(id)}/stop`, { method: "POST" }),
};
