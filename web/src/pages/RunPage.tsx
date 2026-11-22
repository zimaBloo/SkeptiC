import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type Dispatch,
  type KeyboardEvent,
  type PointerEvent,
  type RefObject,
  type SetStateAction,
} from "react";
import { Link, useParams } from "react-router";
import { CRITICS, api, type CriticName, type PanelRecord, type RunDetail } from "../api";
import { FlowCanvas } from "../flow/FlowCanvas";
import type { Intensities } from "../flow/field";
import {
  clampToStage,
  labelRoom,
  labelSide,
  layout,
  type Layout,
  type NodeId,
  type Placement,
  type Point,
} from "../flow/geometry";
import {
  CUT_OFF,
  currentStep,
  formatSeconds,
  statesAt,
  stepSeconds,
  timelineFromEvents,
  timelineFromRecord,
  type StepName,
  type StepState,
  type Timeline,
} from "../flow/timeline";
import { CRITIC_TITLES, modelName } from "../format";
import { useNow, usePlacement, useReducedMotion, useRevealGuard, useRun, useSize } from "../hooks";
import { Verdict } from "./Verdict";

const REPLAY_SPEED = 8;
/** How long the settled field holds, after a live run ends, before the verdict comes in. */
const SETTLE_MS = 1400;

export function RunPage() {
  const { id } = useParams();
  const { run, error, missing } = useRun(id);
  const reduce = useReducedMotion();
  const [stageRef, size] = useSize<HTMLDivElement>();
  const [replayFrom, setReplayFrom] = useState<number | null>(null);
  const [placement, setPlacement] = usePlacement();

  const record = run?.record ?? null;
  const live = run?.status === "running";
  const ended = run !== null && run.status !== "running";
  const replaying = replayFrom !== null && record !== null;
  const timeline = useMemo<Timeline>(
    () =>
      replaying && record
        ? timelineFromRecord(record)
        : timelineFromEvents(run?.events ?? [], ended, run?.finished_at ?? null),
    [replaying, record, run?.events, ended, run?.finished_at],
  );

  // A run that ends while it is being watched holds its settled field for a moment,
  // so the audience sees every stream arrive before the verdict covers them.
  const [settling, setSettling] = useState(false);
  const wasLive = useRef(false);
  useEffect(() => {
    if (live) wasLive.current = true;
    else if (wasLive.current && ended) {
      wasLive.current = false;
      setSettling(true);
    }
  }, [live, ended]);
  useEffect(() => {
    if (!settling) return;
    const timer = window.setTimeout(() => setSettling(false), reduce ? 0 : SETTLE_MS);
    return () => window.clearTimeout(timer);
  }, [settling, reduce]);

  const now = useNow((live && !error) || replaying, 200);
  const at = replaying && timeline.start !== null
    ? timeline.start + Math.max(0, now - (replayFrom ?? now)) * REPLAY_SPEED
    : live
      ? now
      : Number.POSITIVE_INFINITY;
  const states = statesAt(timeline, at);

  useEffect(() => {
    if (replaying && timeline.end !== null && at > timeline.end + 1500) setReplayFrom(null);
  }, [replaying, timeline.end, at]);

  const showResult = ended && !replaying && !settling;
  const intensities = useMemo(() => intensitiesFor(states, showResult), [states, showResult]);
  const l = size.width > 0 ? layout(size.width, size.height, placement) : null;
  const { dragging, dragHandlers } = useNodeDrag(stageRef, placement, setPlacement, !showResult && l !== null);
  const moved = Object.keys(placement).length > 0;
  const verdict = run?.status === "success" && record?.combiner.combined ? record : null;

  return (
    <div className="run-page">
      <FlowCanvas
        className={`run-field${showResult ? " is-dim" : ""}`}
        intensities={intensities}
        still={reduce}
        placement={placement}
        holdPointer={dragging !== null}
      />
      <div className={`run-stage${dragging ? " is-dragging" : ""}`} ref={stageRef}>
        {l && run && (
          <NodeLabels
            layout={l}
            run={run}
            timeline={timeline}
            states={states}
            at={at}
            quiet={showResult}
            dragging={dragging}
            dragHandlers={dragHandlers}
            ideaMoved={placement.idea !== undefined}
          />
        )}
      </div>

      {showResult && run && (
        <div className="run-result">
          <PanelSummary run={run} record={record} onReplay={record ? () => setReplayFrom(Date.now()) : undefined} />
          {verdict ? <Verdict record={verdict} runId={run.id} /> : <RunProblem run={run} />}
        </div>
      )}

      <StatusLine
        id={id}
        run={run}
        error={error}
        missing={missing}
        timeline={timeline}
        states={states}
        at={at}
        replaying={replaying}
        settling={settling}
        onStop={live && id ? () => api.stopRun(id).then(() => undefined) : undefined}
        onResetLayout={moved ? () => setPlacement({}) : undefined}
      />
    </div>
  );
}

type DragHandlers = Partial<{
  onPointerDown: (event: PointerEvent<HTMLDivElement>) => void;
  onPointerMove: (event: PointerEvent<HTMLDivElement>) => void;
  onPointerUp: (event: PointerEvent<HTMLDivElement>) => void;
  onPointerCancel: (event: PointerEvent<HTMLDivElement>) => void;
  onLostPointerCapture: (event: PointerEvent<HTMLDivElement>) => void;
  onDoubleClick: () => void;
  title: string;
}>;

interface Drag {
  id: NodeId;
  pointerId: number;
  /** Where the press began; it becomes a drag only once the pointer has moved a little. */
  startX: number;
  startY: number;
  threshold: number;
  moved: boolean;
  /** Where on the node it was grabbed, so it does not jump to the pointer. */
  dx: number;
  dy: number;
  /** The arrangement before the drag, for Escape. */
  before: Placement;
}

/** How far a press must travel before it moves the node: a click's wobble or a tap
 * should not pin a node in place. */
const DRAG_THRESHOLD = { mouse: 4, other: 8 };

/** Dragging the run page's nodes: grab a node's dot or label and move it; Escape puts
 * it back where the drag began; a double-click returns it to its own place. */
function useNodeDrag(
  stageRef: RefObject<HTMLDivElement | null>,
  placement: Placement,
  setPlacement: Dispatch<SetStateAction<Placement>>,
  enabled: boolean,
): { dragging: NodeId | null; dragHandlers: (id: NodeId, at: Point) => DragHandlers } {
  const [dragging, setDragging] = useState<NodeId | null>(null);
  const drag = useRef<Drag | null>(null);
  // When the last real drag ended: a double-click right after one is the tail of the
  // drag (press, move, release, press), not a request to put the node back.
  const lastMove = useRef(0);
  const end = useCallback(() => {
    if (drag.current?.moved) lastMove.current = performance.now();
    drag.current = null;
    setDragging(null);
  }, []);

  useEffect(() => {
    if (!dragging) return;
    const onKey = (event: globalThis.KeyboardEvent) => {
      if (event.key !== "Escape" || !drag.current) return;
      if (drag.current.moved) setPlacement(drag.current.before);
      end();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [dragging, end, setPlacement]);

  // When the run's result comes in, the nodes fade out; a drag in progress ends there.
  useEffect(() => {
    if (!enabled && drag.current) end();
  }, [enabled, end]);

  const dragHandlers = (id: NodeId, at: Point): DragHandlers => {
    if (!enabled) return {};
    return {
      title: "Drag to move. Double-click to put it back.",
      onPointerDown: (event) => {
        if (event.button !== 0 || drag.current) return;
        const box = stageRef.current?.getBoundingClientRect();
        if (!box) return;
        event.preventDefault();
        event.currentTarget.setPointerCapture(event.pointerId);
        drag.current = {
          id,
          pointerId: event.pointerId,
          startX: event.clientX,
          startY: event.clientY,
          threshold: event.pointerType === "mouse" ? DRAG_THRESHOLD.mouse : DRAG_THRESHOLD.other,
          moved: false,
          dx: event.clientX - box.left - at.x,
          dy: event.clientY - box.top - at.y,
          before: placement,
        };
      },
      onPointerMove: (event) => {
        const current = drag.current;
        if (!current || current.pointerId !== event.pointerId) return;
        if (!current.moved) {
          if (Math.hypot(event.clientX - current.startX, event.clientY - current.startY) < current.threshold) return;
          current.moved = true;
          setDragging(current.id);
        }
        const box = stageRef.current?.getBoundingClientRect();
        if (!box || box.width === 0 || box.height === 0) return;
        const point = clampToStage(box.width, box.height, {
          x: event.clientX - box.left - current.dx,
          y: event.clientY - box.top - current.dy,
        });
        setPlacement((previous) => ({
          ...previous,
          [current.id]: { fx: point.x / box.width, fy: point.y / box.height },
        }));
      },
      onPointerUp: (event) => {
        if (drag.current?.pointerId === event.pointerId) end();
      },
      onPointerCancel: (event) => {
        if (drag.current?.pointerId !== event.pointerId) return;
        if (drag.current.moved) setPlacement(drag.current.before);
        end();
      },
      onLostPointerCapture: (event) => {
        if (drag.current?.pointerId === event.pointerId) end();
      },
      onDoubleClick: () => {
        if (performance.now() - lastMove.current < 800) return;
        setPlacement((previous) => {
          const next = { ...previous };
          delete next[id];
          return next;
        });
      },
    };
  };

  return { dragging, dragHandlers };
}

/** How strongly each stream flows, from where every step stands. Only running steps
 * draw flow: the idea streams into the critic that is writing, and the critics that
 * answered stream into the verdict while it is being combined. Nothing flows to a
 * step that is waiting or over. */
function intensitiesFor(states: Record<StepName, StepState>, settled: boolean): Intensities {
  const out: Intensities = {};
  const combining = states.combiner === "working" && !settled;
  for (const critic of CRITICS) {
    out[`in:${critic}`] = states[critic] === "working" && !settled ? 1 : 0;
    out[`out:${critic}`] = combining && states[critic] === "done" ? 1 : 0;
  }
  return out;
}

interface LabelProps {
  layout: Layout;
  run: RunDetail;
  timeline: Timeline;
  states: Record<StepName, StepState>;
  at: number;
  quiet: boolean;
  dragging: NodeId | null;
  dragHandlers: (id: NodeId, at: Point) => DragHandlers;
  ideaMoved: boolean;
}

/** Where the idea's title sits: centred under its node, or against the screen's edge
 * when the node is near it. On a phone, in its own place, it ends before the critics. */
function ideaLabelBox(l: Layout, moved: boolean): { left: number; width: number; align: "left" | "center" | "right" } {
  const gutter = 16;
  const max = !moved && l.compact ? Math.max(120, l.critics.market.x - 28) : 230;
  const width = Math.max(80, Math.min(max, l.width - 2 * gutter));
  const centred = l.idea.x - width / 2;
  const start = Math.min(Math.max(centred, gutter), l.width - gutter - width);
  const align = start > centred + 0.5 ? "left" : start < centred - 0.5 ? "right" : "center";
  return { left: start - l.idea.x, width, align };
}

function NodeLabels({ layout: l, run, timeline, states, at, quiet, dragging, dragHandlers, ideaMoved }: LabelProps) {
  const lifted = (id: NodeId) => (dragging === id ? " is-dragging" : "");
  const ideaBox = ideaLabelBox(l, ideaMoved);
  // The verdict's label sits to the right of its node; dragged near the right edge it
  // moves to the left (above the node on a phone, growing whichever way has room).
  const verdictRoom = l.width - l.combiner.x - 36;
  const verdictFlip = l.compact ? l.combiner.x < 180 : verdictRoom < 100;
  return (
    <div className={`node-labels${quiet ? " is-quiet" : " is-draggable"}`}>
      <div
        {...dragHandlers("idea", l.idea)}
        className={`node node--idea${lifted("idea")}`}
        style={{ left: l.idea.x, top: l.idea.y }}
      >
        <span className="node-ring" data-state="done" />
        <div
          className="node-text node-text--below node-text--placed"
          style={{
            left: ideaBox.left,
            width: ideaBox.width,
            textAlign: ideaBox.align,
            alignItems: ideaBox.align === "left" ? "flex-start" : ideaBox.align === "right" ? "flex-end" : "center",
          }}
          data-quiet
        >
          <span className="node-title">{run.idea_title}</span>
        </div>
      </div>
      {/* A step's node appears when the step starts, and stays once it has answered. */}
      {CRITICS.filter((critic) => states[critic] !== "waiting").map((critic) => (
        <CriticNode
          key={critic}
          critic={critic}
          at={at}
          state={states[critic]}
          timeline={timeline}
          x={l.critics[critic].x}
          y={l.critics[critic].y}
          room={l.width - l.critics[critic].x - 40 < 120 ? Math.max(60, l.critics[critic].x - 40) : labelRoom(l, critic)}
          side={labelSide(l, critic)}
          flip={l.width - l.critics[critic].x - 40 < 120}
          lifted={dragging === critic}
          handlers={dragHandlers(critic, l.critics[critic])}
        />
      ))}
      {states.combiner !== "waiting" && (
        <div
          {...dragHandlers("combiner", l.combiner)}
          className={`node node--combiner${lifted("combiner")}`}
          style={
            {
              left: l.combiner.x,
              top: l.combiner.y,
              "--room": `${Math.max(60, verdictFlip ? l.combiner.x - 40 : verdictRoom)}px`,
            } as CSSProperties
          }
        >
          <span className="node-ring" data-state={states.combiner} />
          {/* Every stream reaches the verdict from the left, so its label sits to the
              right (above it on a phone), in short lines that fit the space there. */}
          <div
            className={`node-text ${l.compact ? "node-text--above" : "node-text--right"}${verdictFlip ? (l.compact ? " is-start" : " is-left") : ""}`}
            data-quiet
          >
            <span className="node-title">Verdict</span>
            {l.compact ? (
              // On a phone two lines, so the label stays clear of the critics' labels.
              <span className="node-meta mono">
                {modelName(timeline.steps.combiner.model)} ·{" "}
                {verdictState(states.combiner, stepSeconds(timeline.steps.combiner, at))}
              </span>
            ) : (
              <>
                <span className="node-meta mono">{modelName(timeline.steps.combiner.model)}</span>
                <span className="node-meta mono">
                  {verdictState(states.combiner, stepSeconds(timeline.steps.combiner, at))}
                </span>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function CriticNode(props: {
  critic: CriticName;
  state: StepState;
  timeline: Timeline;
  at: number;
  x: number;
  y: number;
  room: number;
  side: "above" | "below";
  /** Near the right edge the label goes to the left of the node. */
  flip: boolean;
  lifted: boolean;
  handlers: DragHandlers;
}) {
  const { critic, state, timeline, at, x, y, room, side, flip, lifted, handlers } = props;
  const step = timeline.steps[critic];
  const seconds = stepSeconds(step, at);
  let meta = stepLine(state, step.model, seconds);
  if (state === "failed" && step.error === CUT_OFF) meta = `${modelName(step.model)} · ${CUT_OFF}`;
  if (state === "done" && step.issues !== null) {
    meta = `${modelName(step.model)} · ${step.issues} issues · ${formatSeconds(seconds)}`;
  }
  return (
    <div
      {...handlers}
      className={`node node--critic${lifted ? " is-dragging" : ""}`}
      data-critic={critic}
      data-state={state}
      style={{ left: x, top: y, "--room": `${room}px` } as CSSProperties}
    >
      <span className="node-ring" data-state={state} />
      <div className={`node-text node-text--right is-${side}${flip ? " is-left" : ""}`} data-quiet>
        <span className="node-title">{CRITIC_TITLES[critic]}</span>
        <span className="node-meta mono">{meta}</span>
      </div>
    </div>
  );
}

function verdictState(state: StepState, seconds: number | null): string {
  switch (state) {
    case "waiting":
      return "waiting";
    case "working":
      return `writing ${formatSeconds(seconds)}`;
    case "done":
      return formatSeconds(seconds);
    case "failed":
      return "no answer";
  }
}

function stepLine(state: StepState, model: string | null, seconds: number | null): string {
  const name = modelName(model);
  switch (state) {
    case "waiting":
      return name ? `${name} · waiting` : "waiting";
    case "working":
      return `${name ? `${name} · ` : ""}writing ${formatSeconds(seconds)}`;
    case "done":
      return `${name ? `${name} · ` : ""}${formatSeconds(seconds)}`;
    case "failed":
      return `${name ? `${name} · ` : ""}no usable answer`;
  }
}

function StatusLine(props: {
  id: string | undefined;
  run: RunDetail | null;
  error: string | null;
  missing: boolean;
  timeline: Timeline;
  states: Record<StepName, StepState>;
  at: number;
  replaying: boolean;
  settling: boolean;
  onStop?: () => Promise<void>;
  onResetLayout?: () => void;
}) {
  const { id, run, error, missing, timeline, states, at, replaying, settling, onStop, onResetLayout } = props;
  const lineRef = useRef<HTMLDivElement>(null);
  if (missing) {
    return (
      <p className="status-line" role="alert">
        There is no run called {id}. <Link to="/">Start a new run</Link>
      </p>
    );
  }
  if (error && !run) return <p className="status-line" role="alert">Cannot reach the panel's server: {error}</p>;
  if (!run) return <p className="status-line">Loading the run…</p>;
  if (run.status !== "running" && !replaying && !settling) return null;
  if (error && run.status === "running") {
    return (
      <p className="status-line" role="alert">
        Lost contact with the panel's server ({error}). Trying again every few seconds.
      </p>
    );
  }

  const elapsed =
    timeline.start !== null ? Math.max(0, (Math.min(at, timeline.end ?? at) - timeline.start) / 1000) : null;
  const step = currentStep(states);
  let what = settling ? "The run ended before the first critic started" : "Starting the panel and loading the first model…";
  if (step) {
    const state = states[step];
    const who = step === "combiner" ? "The combining step" : `${CRITIC_TITLES[step]} critic`;
    if (state === "failed" && timeline.steps[step].error === CUT_OFF) what = `${who} was ${CUT_OFF}`;
    else if (step === "combiner") what = state === "working" ? "Combining the four reviews" : state === "done" ? "The panel has finished" : `${who} gave no usable answer`;
    else what = `${who} ${state === "working" ? "is writing" : state === "failed" ? "gave no usable answer" : "is done"}`;
  }
  const label = replaying
    ? `Replay ${REPLAY_SPEED}×`
    : !settling
      ? "Running"
      : run.status === "success"
        ? "Finished"
        : run.status === "stopped"
          ? "Stopped"
          : "Ended";

  return (
    <div className="status-line" ref={lineRef} tabIndex={-1}>
      <span className="mono">{label}</span>
      <span className="mono">{formatSeconds(elapsed)}</span>
      <span role="status">{what}</span>
      {onStop && !settling && <StopControl onStop={onStop} />}
      {onResetLayout && (
        <button
          type="button"
          className="text-button"
          onClick={() => {
            // The button goes away once nothing is moved; keep focus in the status line.
            lineRef.current?.focus();
            onResetLayout();
          }}
        >
          Reset layout
        </button>
      )}
    </div>
  );
}

/** Stopping throws away minutes of model time, so it takes a second, deliberate click. */
function StopControl({ onStop }: { onStop: () => Promise<void> }) {
  const [asking, setAsking] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const keepRef = useRef<HTMLButtonElement>(null);
  const stopRef = useRef<HTMLButtonElement>(null);
  const wasAsking = useRef(false);

  // When the question goes away without a stop, focus goes back to where it started.
  useEffect(() => {
    if (asking) wasAsking.current = true;
    else if (wasAsking.current) {
      wasAsking.current = false;
      stopRef.current?.focus();
    }
  }, [asking]);

  useEffect(() => {
    if (!asking) return;
    keepRef.current?.focus();
    // An unanswered question goes away on its own.
    const timer = window.setTimeout(() => setAsking(false), 8000);
    return () => window.clearTimeout(timer);
  }, [asking]);

  const confirm = async () => {
    setAsking(false);
    setStopping(true);
    try {
      // On success "Stopping…" stays until the next poll shows the run ended,
      // which removes this control.
      await onStop();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setStopping(false);
    }
  };

  if (stopping) return <span className="stop-control muted">Stopping…</span>;
  if (asking) {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setAsking(false);
    };
    return (
      <span className="stop-control" role="group" aria-label="Stop the run?" onKeyDown={onKeyDown}>
        <span>Stop the run? It cannot be resumed.</span>
        <button type="button" className="text-button" onClick={confirm}>
          Stop it
        </button>
        <button ref={keepRef} type="button" className="text-button" onClick={() => setAsking(false)}>
          Keep running
        </button>
      </span>
    );
  }
  return (
    <span className="stop-control">
      <button
        ref={stopRef}
        type="button"
        className="text-button"
        onClick={() => {
          setError(null);
          setAsking(true);
        }}
      >
        Stop run
      </button>
      {error && (
        <span className="form-error" role="alert">
          Could not stop it: {error}
        </span>
      )}
    </span>
  );
}

function PanelSummary({ run, record, onReplay }: { run: RunDetail; record: PanelRecord | null; onReplay?: () => void }) {
  const ref = useRevealGuard<HTMLElement>();
  const start = record?.started_at ?? null;
  const end = record?.finished_at ?? null;
  const seconds = start && end ? (Date.parse(end) - Date.parse(start)) / 1000 : null;
  return (
    <aside ref={ref} className="panel-summary" aria-label="The run">
      <h1 className="panel-idea">{run.idea_title}</h1>
      {record && (
        <ul className="panel-critics">
          {record.critics.map((critic) => (
            <li key={critic.critic} data-critic={critic.critic}>
              <span className="critic-dot" />
              <span>{CRITIC_TITLES[critic.critic]}</span>
              <span className="mono">{critic.verdict ? critic.verdict.overall : "no answer"}</span>
            </li>
          ))}
        </ul>
      )}
      <p className="panel-meta mono">
        {seconds !== null ? `${formatSeconds(seconds)} · ` : ""}four models · every call logged
      </p>
      <div className="panel-actions">
        <Link to={`/runs/${run.id}/details`}>Each critic's issues</Link>
        {onReplay && (
          <button type="button" className="text-button" onClick={onReplay}>
            Replay the run
          </button>
        )}
      </div>
    </aside>
  );
}

function RunProblem({ run }: { run: RunDetail }) {
  const message =
    run.status === "stopped"
      ? "The run was stopped before the panel finished."
      : run.error ?? run.record?.error ?? "The panel did not finish.";
  return (
    <section className="verdict verdict--problem" aria-live="polite">
      <p className="verdict-lede">No verdict for this run.</p>
      <p className="verdict-rest">{message}</p>
      {run.record && (
        <p className="verdict-rest">
          <Link to={`/runs/${run.id}/details`}>See what each critic said</Link>
        </p>
      )}
    </section>
  );
}
