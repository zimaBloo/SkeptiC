import { describe, expect, it } from "vitest";
import type { ProgressEvent } from "../api";
import { CUT_OFF, currentStep, formatSeconds, statesAt, stepSeconds, timelineFromEvents } from "./timeline";

const at = (s: number) => new Date(Date.UTC(2026, 10, 21, 12, 0, s)).toISOString();
const t = (s: number) => Date.parse(at(s));

const EVENTS: ProgressEvent[] = [
  { time: at(0), event: "run_started", critics: ["market", "technical", "customer", "risk"], models: { market: "ollama/qwen", combiner: "ollama/gemma" } },
  { time: at(1), event: "critic_started", critic: "market", model: "ollama/qwen" },
  { time: at(60), event: "critic_finished", critic: "market", ok: true, issues: 5, overall: "weak", error: null },
  { time: at(61), event: "critic_started", critic: "technical", model: "ollama/gemma" },
  { time: at(90), event: "critic_finished", critic: "technical", ok: false, issues: null, overall: null, error: "no JSON object" },
];

describe("timeline from progress events", () => {
  const timeline = timelineFromEvents(EVENTS);

  it("reads models, starts, ends and outcomes", () => {
    expect(timeline.start).toBe(t(0));
    expect(timeline.steps.combiner.model).toBe("ollama/gemma");
    expect(timeline.steps.market).toMatchObject({ start: t(1), end: t(60), ok: true, issues: 5 });
    expect(timeline.steps.technical.error).toBe("no JSON object");
  });

  it("gives each step's state at a moment", () => {
    expect(statesAt(timeline, t(30))).toMatchObject({ market: "working", technical: "waiting" });
    expect(statesAt(timeline, t(70))).toMatchObject({ market: "done", technical: "working" });
    expect(statesAt(timeline, t(95))).toMatchObject({ technical: "failed", customer: "waiting" });
  });

  it("marks a step that never reported back as failed when the run ends", () => {
    const crashed = timelineFromEvents([
      ...EVENTS,
      { time: at(91), event: "critic_started", critic: "customer", model: "ollama/ministral" },
      { time: at(95), event: "run_finished", ok: false, error: "connection refused" },
    ]);
    expect(statesAt(crashed, t(96)).customer).toBe("failed");
    expect(crashed.steps.customer.error).toBe("connection refused");
  });

  it("names the step to read about and its time", () => {
    expect(currentStep(statesAt(timeline, t(30)))).toBe("market");
    expect(currentStep(statesAt(timeline, t(95)))).toBe("technical");
    expect(stepSeconds(timeline.steps.market, t(30))).toBe(29);
    expect(stepSeconds(timeline.steps.market, t(200))).toBe(59);
    expect(formatSeconds(125.7)).toBe("2:05");
  });
});

describe("a run cut off without run_finished", () => {
  const cut: ProgressEvent[] = [
    { time: at(0), event: "run_started", critics: ["market", "technical", "customer", "risk"], models: {} },
    { time: at(1), event: "critic_started", critic: "market", model: "ollama/qwen" },
  ];

  it("closes the open step when the run is known to be over", () => {
    const timeline = timelineFromEvents(cut, true);
    expect(timeline.steps.market).toMatchObject({ end: t(1), ok: false, error: CUT_OFF });
    expect(statesAt(timeline, Number.POSITIVE_INFINITY)).toMatchObject({ market: "failed", technical: "waiting" });
    expect(formatSeconds(stepSeconds(timeline.steps.market, Number.POSITIVE_INFINITY))).toBe("0:00");
  });

  it("leaves a running run's step open", () => {
    expect(statesAt(timelineFromEvents(cut), t(30)).market).toBe("working");
  });

  it("never prints an infinite or negative time", () => {
    expect(formatSeconds(Number.POSITIVE_INFINITY)).toBe("");
    expect(formatSeconds(-240)).toBe("0:00");
  });
});
