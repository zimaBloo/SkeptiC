import { useEffect, useRef, useState, type Dispatch, type RefObject, type SetStateAction } from "react";
import { ApiError, CRITICS, api, type RunDetail } from "./api";
import type { NodeId, Placement } from "./flow/geometry";

/** A run, polled once a second while it is running. `missing` means the server
 * answered that there is no such run, so polling stops. */
export function useRun(id: string | undefined): { run: RunDetail | null; error: string | null; missing: boolean } {
  const [run, setRun] = useState<RunDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [missing, setMissing] = useState(false);

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    let timer: number | undefined;
    const load = async () => {
      try {
        const detail = await api.run(id);
        if (cancelled) return;
        setRun(detail);
        setError(null);
        if (detail.status === "running") timer = window.setTimeout(load, 1000);
      } catch (e) {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : String(e));
        // A bad or unknown run id will not get better by asking again.
        if (e instanceof ApiError && (e.status === 404 || e.status === 422)) {
          setMissing(true);
          return;
        }
        timer = window.setTimeout(load, 3000);
      }
    };
    void load();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [id]);

  return { run, error, missing };
}

/** The current time, refreshed every `interval` ms while `active`. */
export function useNow(active: boolean, interval = 250): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => setNow(Date.now()), interval);
    return () => window.clearInterval(timer);
  }, [active, interval]);
  return now;
}

/** An element's size in CSS px. */
export function useSize<T extends Element>(): [RefObject<T | null>, { width: number; height: number }] {
  const ref = useRef<T>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => {
      if (!entry) return;
      const { width, height } = entry.contentRect;
      setSize({ width, height });
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  return [ref, size];
}

export function useReducedMotion(): boolean {
  const [reduce, setReduce] = useState(
    () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false,
  );
  useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const onChange = () => setReduce(query.matches);
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);
  return reduce;
}

/** Finish the entrance animations inside an element if they have not run after `ms`,
 * counted again whenever `trigger` changes (a new page, say).
 * Content must never stay hidden because a browser paused its animation clock
 * (a background tab, a screenshot tool, a frozen projector session).
 * `ms` must outlast the longest entrance (the verdict's, about 2.2 s). */
export function useRevealGuard<T extends Element>(trigger?: unknown, ms = 4000): RefObject<T | null> {
  const ref = useRef<T>(null);
  useEffect(() => {
    const timer = window.setTimeout(() => {
      const root = ref.current;
      if (!root) return;
      for (const animation of root.getAnimations({ subtree: true })) {
        const end = animation.effect?.getComputedTiming().endTime;
        // Endless animations (a working step's pulse) cannot be finished, and need not be.
        if (animation.playState !== "finished" && end !== undefined && Number.isFinite(Number(end))) {
          animation.finish();
        }
      }
    }, ms);
    return () => window.clearTimeout(timer);
  }, [trigger, ms]);
  return ref;
}

const PLACEMENT_KEY = "skeptic.placement";
const NODE_IDS: NodeId[] = ["idea", ...CRITICS, "combiner"];

/** Where the viewer has dragged the run page's nodes. Kept for the browser tab, so an
 * arrangement survives a reload and carries over to the next run. */
export function usePlacement(): [Placement, Dispatch<SetStateAction<Placement>>] {
  const [placement, setPlacement] = useState<Placement>(readPlacement);
  useEffect(() => {
    try {
      sessionStorage.setItem(PLACEMENT_KEY, JSON.stringify(placement));
    } catch {
      // Storage can be off (private windows, blocked site data); the layout still works.
    }
  }, [placement]);
  return [placement, setPlacement];
}

function readPlacement(): Placement {
  try {
    const parsed: unknown = JSON.parse(sessionStorage.getItem(PLACEMENT_KEY) ?? "{}");
    const placement: Placement = {};
    if (!parsed || typeof parsed !== "object") return placement;
    for (const id of NODE_IDS) {
      const value = (parsed as Record<string, { fx?: unknown; fy?: unknown } | undefined>)[id];
      const fx = value?.fx;
      const fy = value?.fy;
      if (typeof fx === "number" && typeof fy === "number" && fx >= 0 && fx <= 1 && fy >= 0 && fy <= 1) {
        placement[id] = { fx, fy };
      }
    }
    return placement;
  } catch {
    return {};
  }
}
