import type { CriticName, Overall, Severity } from "./api";

export const CRITIC_TITLES: Record<CriticName, string> = {
  market: "Market",
  technical: "Technical feasibility",
  customer: "Customer value",
  risk: "Risk and regulation",
};

export const CRITIC_SHORT: Record<CriticName, string> = {
  market: "market",
  technical: "technical",
  customer: "customer",
  risk: "risk",
};

/** "ollama/qwen3.5:9b-q4_K_M" -> "Qwen 3.5 9B". Unknown names pass through shortened. */
export function modelName(model: string | null | undefined): string {
  if (!model) return "";
  const bare = model.replace(/^[a-z-]+\//, "");
  const known: [RegExp, string][] = [
    [/^qwen(\d+(?:\.\d+)?):(\d+)b/i, "Qwen $1 $2B"],
    [/^gemma(\d+):(\d+)b/i, "Gemma $1 $2B"],
    [/^ministral-(\d+):(\d+)b/i, "Ministral $1 $2B"],
    [/^nemotron-(\d+)-nano:(\d+)b/i, "Nemotron $1 Nano $2B"],
  ];
  for (const [pattern, replacement] of known) {
    if (pattern.test(bare)) return bare.replace(pattern, replacement).replace(/-.*$|\s*$/, "");
  }
  return bare.split(":")[0] ?? bare;
}

export const OVERALL_LABEL: Record<Overall, string> = {
  strong: "strong",
  promising: "promising",
  weak: "weak",
  "not viable": "not viable",
};

export const SEVERITY_ORDER: Record<Severity, number> = { fatal: 0, major: 1, minor: 2 };

export function formatWhen(iso: string | null): string {
  if (!iso) return "";
  const date = new Date(iso);
  return date.toLocaleString(undefined, {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function durationBetween(start: string | null, end: string | null): number | null {
  if (!start || !end) return null;
  return (Date.parse(end) - Date.parse(start)) / 1000;
}
