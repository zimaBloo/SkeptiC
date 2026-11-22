// When each step of a run started and ended, read from live progress events or
// from a finished record, and what state every step was in at a given moment.

import { CRITICS, type CriticName, type Overall, type PanelRecord, type ProgressEvent } from "../api";

export type StepState = "waiting" | "working" | "done" | "failed";
export type StepName = CriticName | "combiner";

export interface Step {
  start: number | null; // epoch ms
  end: number | null;
  ok: boolean | null;
  model: string | null;
  issues: number | null;
  overall: Overall | null;
  error: string | null;
}

export interface Timeline {
  start: number | null;
  end: number | null;
  ok: boolean | null;
  steps: Record<StepName, Step>;
}

const STEPS: StepName[] = [...CRITICS, "combiner"];

/** The error of a step that was still working when its run was stopped or killed. */
export const CUT_OFF = "stopped before it answered";

function emptyStep(): Step {
  return { start: null, end: null, ok: null, model: null, issues: null, overall: null, error: null };
}

function emptyTimeline(): Timeline {
  const steps = Object.fromEntries(STEPS.map((name) => [name, emptyStep()])) as Record<StepName, Step>;
  return { start: null, end: null, ok: null, steps };
}

const ms = (iso: string | null | undefined): number | null => (iso ? Date.parse(iso) : null);

/** The timeline of a run, from the panel's progress events. `ended` says the run is
 * over even if it never wrote run_finished (stopped, or killed with the server);
 * `endedAt` is when the server saw it end, if it knows. */
export function timelineFromEvents(events: ProgressEvent[], ended = false, endedAt: string | null = null): Timeline {
  const timeline = emptyTimeline();
  for (const event of events) {
    const at = ms(event.time);
    switch (event.event) {
      case "run_started":
        timeline.start = at;
        for (const [role, model] of Object.entries(event.models)) {
          if (role in timeline.steps) timeline.steps[role as StepName].model = model;
        }
        break;
      case "critic_started":
        Object.assign(timeline.steps[event.critic], { start: at, model: event.model });
        break;
      case "critic_finished":
        Object.assign(timeline.steps[event.critic], {
          end: at,
          ok: event.ok,
          issues: event.issues,
          overall: event.overall,
          error: event.error,
        });
        break;
      case "combiner_started":
        Object.assign(timeline.steps.combiner, { start: at, model: event.model });
        break;
      case "combiner_finished":
        Object.assign(timeline.steps.combiner, { end: at, ok: event.ok, error: event.error });
        break;
      case "run_finished":
        timeline.end = at;
        timeline.ok = event.ok;
        // A step that started but never reported back failed with the run.
        for (const name of STEPS) {
          const step = timeline.steps[name];
          if (step.start !== null && step.end === null) {
            Object.assign(step, { end: at, ok: false, error: event.error });
          }
        }
        break;
      case "repair_started":
        break;
    }
  }
  if (ended && timeline.end === null) {
    // No run_finished: the run was cut off. Close every open step when it ended: the
    // server's time if it has one, else the last event.
    const times = [ms(events.at(-1)?.time), ms(endedAt)].filter((t): t is number => t !== null);
    const last = times.length ? Math.max(...times) : null;
    timeline.end = last;
    timeline.ok = false;
    for (const name of STEPS) {
      const step = timeline.steps[name];
      if (step.start !== null && step.end === null) Object.assign(step, { end: last, ok: false, error: CUT_OFF });
    }
  }
  return timeline;
}


/** The timeline of a finished run, from its record's timings. */
export function timelineFromRecord(record: PanelRecord): Timeline {
  const timeline = emptyTimeline();
  for (const critic of record.critics) {
    timeline.steps[critic.critic] = {
      start: ms(critic.timing.started_at),
      end: ms(critic.timing.finished_at),
      ok: critic.verdict !== null,
      model: critic.model,
      issues: critic.verdict?.issues.length ?? null,
      overall: critic.verdict?.overall ?? null,
      error: critic.error,
    };
  }
  const combiner = record.combiner;
  timeline.steps.combiner = {
    ...emptyStep(),
    start: ms(combiner.timing.started_at),
    end: ms(combiner.timing.finished_at),
    ok: combiner.combined !== null,
    model: combiner.model,
    error: combiner.error,
  };
  const starts = STEPS.map((name) => timeline.steps[name].start).filter((t): t is number => t !== null);
  timeline.start = ms(record.started_at) ?? (starts.length ? Math.min(...starts) : null);
  timeline.end = ms(record.finished_at) ?? timeline.steps.combiner.end;
  timeline.ok = record.status === "success";
  return timeline;
}

/** Each step's state at time `at` (epoch ms). */
export function statesAt(timeline: Timeline, at: number): Record<StepName, StepState> {
  return Object.fromEntries(
    STEPS.map((name) => {
      const step = timeline.steps[name];
      let state: StepState = "waiting";
      if (step.start !== null && step.start <= at) state = "working";
      if (step.end !== null && step.end <= at) state = step.ok ? "done" : "failed";
      return [name, state];
    }),
  ) as Record<StepName, StepState>;
}

/** Seconds a step has been working at `at`, or took in all once it ended. */
export function stepSeconds(step: Step, at: number): number | null {
  if (step.start === null || step.start > at) return null;
  const end = step.end !== null && step.end <= at ? step.end : at;
  return Math.max(0, (end - step.start) / 1000);
}

/** The step a viewer should read about now: the working one, else the last to finish. */
export function currentStep(states: Record<StepName, StepState>): StepName | null {
  const working = STEPS.find((name) => states[name] === "working");
  if (working) return working;
  const ended = STEPS.filter((name) => states[name] === "done" || states[name] === "failed");
  return ended.at(-1) ?? null;
}

export function formatSeconds(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds)) return "";
  const whole = Math.max(0, Math.floor(seconds));
  const minutes = Math.floor(whole / 60);
  const rest = String(whole % 60).padStart(2, "0");
  return `${minutes}:${rest}`;
}
