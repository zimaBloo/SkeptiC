import type { CriticName, Overall, Severity } from "./api";

export const CRITIC_TITLES: Record<CriticName, string> = {
  market: "Market",
  technical: "Technical feasibility",
  customer: "Customer value",
  risk: "Risk and regulation",
};

/** What each critic looks at, in a line (from its prompt in src/skeptic/prompts/roles). */
export const CRITIC_ANGLES: Record<CriticName, string> = {
  market: "Demand, competitors, market size and pricing, and whether the price covers the cost.",
  technical: "Whether it can be built and work as described, and what it costs to build and run.",
  customer: "Who has the problem, how painful it is, and whether they would pay and keep using it.",
  risk: "Legal, privacy, safety and ethical risks, and dependencies that could stop the idea.",
};

export const CRITIC_SHORT: Record<CriticName, string> = {
  market: "market",
  technical: "technical",
  customer: "customer",
  risk: "risk",
};

/** "ollama/qwen3.5:9b-q4_K_M" -> "Qwen 3.5 9B", "openrouter/openai/gpt-6.2-sol" -> "GPT-6.2 Sol".
 * Unknown names pass through shortened. */
export function modelName(model: string | null | undefined): string {
  if (!model) return "";
  // Drop the provider path: "ollama/", "openrouter/anthropic/" and the like.
  const bare = model.replace(/^(?:[a-z0-9-]+\/)+/, "");
  const word = (w: string) => w.charAt(0).toUpperCase() + w.slice(1);
  const known: [RegExp, (...groups: string[]) => string][] = [
    [/^qwen(\d+(?:\.\d+)?):(\d+)b/i, (v, b) => `Qwen ${v} ${b}B`],
    [/^gemma(\d+):(\d+)b/i, (v, b) => `Gemma ${v} ${b}B`],
    [/^ministral-(\d+):(\d+)b/i, (v, b) => `Ministral ${v} ${b}B`],
    [/^nemotron-(\d+)-nano:(\d+)b/i, (v, b) => `Nemotron ${v} Nano ${b}B`],
    [/^gpt-(\d+(?:\.\d+)?[a-z]?)(?:-([a-z]+))?/i, (v, tier) => `GPT-${v}${tier ? ` ${word(tier)}` : ""}`],
    // Version 4-5 means 4.5; a date such as 20250514 is not part of it.
    [/^claude-([a-z]+)-(\d+(?:[.-]\d{1,2})?)(?!\d)/i, (tier, v) => `Claude ${word(tier ?? "")} ${(v ?? "").replace("-", ".")}`],
    [/^gemini-(\d+(?:\.\d+)?)-([a-z]+)/i, (v, tier) => `Gemini ${v} ${word(tier ?? "")}`],
    [/^grok-(\d+(?:\.\d+)?)/i, (v) => `Grok ${v}`],
  ];
  for (const [pattern, name] of known) {
    const match = pattern.exec(bare);
    if (match) return name(...match.slice(1).map((g) => g ?? ""));
  }
  return bare.split(":")[0] ?? bare;
}

const COUNTS = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"];

/** 4 -> "four", 12 -> "12". */
export function countWord(n: number): string {
  return COUNTS[n] ?? String(n);
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

const LABS: Record<string, string> = {
  openai: "OpenAI",
  anthropic: "Anthropic",
  google: "Google",
  "x-ai": "xAI",
  mistralai: "Mistral",
  deepseek: "DeepSeek",
};

/** Who runs a model: the lab behind a hosted model, or this computer for a local one. */
export function modelHost(model: string | null | undefined): string {
  if (!model) return "";
  if (model.startsWith("ollama/")) return "on this computer";
  const lab = model.replace(/^openrouter\//, "").split("/")[0] ?? "";
  return LABS[lab] ?? lab;
}

/** 12345 -> "12,345". */
export function formatTokens(tokens: number | null | undefined): string {
  return tokens === null || tokens === undefined ? "" : Math.round(tokens).toLocaleString("en-US");
}

/** 0.0254 -> "$0.025"; under a tenth of a cent -> "<$0.001". */
export function formatCost(cost: number | null | undefined): string {
  if (cost === null || cost === undefined) return "";
  if (cost > 0 && cost < 0.001) return "<$0.001";
  return "$" + cost.toFixed(cost < 1 ? 3 : 2);
}
