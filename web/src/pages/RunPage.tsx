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
import { Link } from "react-router";
import { CRITICS, api, type CriticName, type PanelRecord, type RunDetail } from "../api";
import type { Clouds, Intensities } from "../flow/field";
import { FlowCanvas } from "../flow/FlowCanvas";
import { Graticule } from "../flow/Graticule";
import {
  clampToStage,
  labelRoom,
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
  runTotals,
  statesAt,
  stepSeconds,
  stepTokens,
  timelineFromEvents,
  timelineFromRecord,
  type Step,
  type StepName,
  type StepState,
  type Timeline,
} from "../flow/timeline";
import {
  CRITIC_ANGLES,
  CRITIC_SHORT,
  CRITIC_TITLES,
  countWord,
  formatCost,
  formatTokens,
  modelHost,
  modelName,
} from "../format";
import { useNow, usePlacement, useReducedMotion, useRevealGuard, useSize } from "../hooks";
import { Verdict } from "./Verdict";

const REPLAY_SPEED = 8;
/** How long the settled field holds, after a live run ends, before the verdict comes in. */
const SETTLE_MS = 1400;

/** Where a run stands at the moment shown: live, settling, replaying or finished. */
export interface RunView {
  timeline: Timeline;
  states: Record<StepName, StepState>;
  at: number;
  live: boolean;
  ended: boolean;
  replaying: boolean;
  settling: boolean;
  /** The verdict and the rest of the result are shown. */
  showResult: boolean;
  /** The run ended while being watched, so the page may bring the verdict into view. */
  watched: boolean;
  startReplay: () => void;
  reduce: boolean;
}

export function useRunView(run: RunDetail | null): RunView {
  const reduce = useReducedMotion();
  const [replayFrom, setReplayFrom] = useState<number | null>(null);
  const record = run?.record ?? null;
  const live = run?.status === "running";
  const ended = run !== null && run.status !== "running";
  const replaying = replayFrom !== null && record !== null;
  const timeline = useMemo<Timeline>(() => {
    if (replaying && record) return timelineFromRecord(record);
    const live = timelineFromEvents(run?.events ?? [], ended, run?.finished_at ?? null);
    if (!record) return live;
    // A finished run's record knows what older runs' progress events did not carry:
    // each critic's top issue, and the tokens and cost of every step.
    const kept = timelineFromRecord(record);
    for (const name of [...CRITICS, "combiner"] as StepName[]) {
      const step = live.steps[name];
      const known = kept.steps[name];
      step.topIssue ??= known.topIssue;
      step.usage ??= known.usage;
      step.points ??= known.points;
    }
    return live;
  }, [replaying, record, run?.events, ended, run?.finished_at]);

  // A run that ends while it is being watched holds its settled field for a moment,
  // so the audience sees every stream arrive before the verdict comes in below.
  const [settling, setSettling] = useState(false);
  const [watched, setWatched] = useState(false);
  const wasLive = useRef(false);
  useEffect(() => {
    if (live) wasLive.current = true;
    else if (wasLive.current && ended) {
      wasLive.current = false;
      setSettling(true);
      setWatched(true);
    }
  }, [live, ended]);
  useEffect(() => {
    if (!settling) return;
    const timer = window.setTimeout(() => setSettling(false), reduce ? 0 : SETTLE_MS);
    return () => window.clearTimeout(timer);
  }, [settling, reduce]);

  const now = useNow(live || replaying, 200);
  const at =
    replaying && timeline.start !== null
      ? timeline.start + Math.max(0, now - (replayFrom ?? now)) * REPLAY_SPEED
      : live
        ? now
        : Number.POSITIVE_INFINITY;
  const states = statesAt(timeline, at);

  useEffect(() => {
    if (replaying && timeline.end !== null && at > timeline.end + 1500) setReplayFrom(null);
  }, [replaying, timeline.end, at]);

  return {
    timeline,
    states,
    at,
    live,
    ended,
    replaying,
    settling,
    showResult: ended && !replaying && !settling,
    watched,
    startReplay: () => setReplayFrom(Date.now()),
    reduce,
  };
}

/** How strongly each stream flows, from where every step stands. Only running steps
 * draw flow: the idea streams into each critic that is writing, and the critics that
 * answered stream into the verdict while it is being combined. */
function intensitiesFor(states: Record<StepName, StepState>): Intensities {
  const out: Intensities = {};
  const combining = states.combiner === "working";
  for (const critic of CRITICS) {
    out[`in:${critic}`] = states[critic] === "working" ? 1 : 0;
    out[`out:${critic}`] = combining && states[critic] === "done" ? 1 : 0;
  }
  return out;
}

/** A finished step's cloud: bigger the more tokens it used. A step that does not report
 * its tokens gets a modest cloud. */
export function cloudSize(tokens: number | null): number {
  if (tokens === null) return 0.35;
  return Math.min(1, Math.max(0.18, Math.sqrt(tokens / 9000)));
}

function cloudsFor(timeline: Timeline, states: Record<StepName, StepState>): Clouds {
  const clouds: Clouds = {};
  for (const name of [...CRITICS, "combiner"] as StepName[]) {
    if (states[name] === "done") clouds[name] = cloudSize(stepTokens(timeline.steps[name]));
  }
  return clouds;
}

/** The panel at work, top to bottom: the idea, the four critics, the verdict. */
export function RunStage(props: {
  id: string;
  run: RunDetail | null;
  error: string | null;
  missing: boolean;
  view: RunView;
}) {
  const { id, run, error, missing, view } = props;
  const { timeline, states, at, live, replaying, settling, reduce } = view;
  const [stageRef, size] = useSize<HTMLDivElement>();
  const [placement, setPlacement] = usePlacement();
  const [open, setOpen] = useState<NodeId | null>(null);
  const intensities = useMemo(() => intensitiesFor(states), [states]);
  const clouds = useMemo(() => cloudsFor(timeline, states), [timeline, states]);
  const l = size.width > 0 ? layout(size.width, size.height, placement) : null;
  const { dragging, dragHandlers } = useNodeDrag(stageRef, placement, setPlacement, l !== null, setOpen);
  const moved = Object.keys(placement).length > 0;

  // A card closes with Escape or a click anywhere outside the nodes and cards.
  useEffect(() => {
    if (!open) return;
    const onKey = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") setOpen(null);
    };
    const onDown = (event: globalThis.PointerEvent) => {
      const target = event.target as HTMLElement | null;
      if (!target?.closest(".node, .node-card")) setOpen(null);
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("pointerdown", onDown);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("pointerdown", onDown);
    };
  }, [open]);

  return (
    <div className={`run-stage${dragging ? " is-dragging" : ""}`} ref={stageRef}>
      <Graticule />
      <FlowCanvas
        className="run-field"
        intensities={intensities}
        clouds={clouds}
        settled={!live && !replaying}
        still={reduce}
        placement={placement}
        holdPointer={dragging !== null}
      />
      {l && run && (
        <NodeLabels
          layout={l}
          run={run}
          timeline={timeline}
          states={states}
          at={at}
          dragging={dragging}
          dragHandlers={dragHandlers}
          open={open}
        />
      )}
      {l && run && open && (
        <NodeCard
          id={open}
          layout={l}
          run={run}
          timeline={timeline}
          states={states}
          at={at}
          onClose={() => setOpen(null)}
        />
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
        onStop={live ? () => api.stopRun(id).then(() => undefined) : undefined}
        onResetLayout={moved ? () => setPlacement({}) : undefined}
      />
    </div>
  );
}

type DragHandlers = Partial<{
  onPointerDown: (event: PointerEvent<HTMLElement>) => void;
  onPointerMove: (event: PointerEvent<HTMLElement>) => void;
  onPointerUp: (event: PointerEvent<HTMLElement>) => void;
  onPointerCancel: (event: PointerEvent<HTMLElement>) => void;
  onLostPointerCapture: (event: PointerEvent<HTMLElement>) => void;
  onDoubleClick: () => void;
  onClick: () => void;
  onKeyDown: (event: KeyboardEvent<HTMLElement>) => void;
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

/** The run's nodes: a click (or Enter) opens the node's card; a press that travels a few
 * pixels drags the node instead. Escape puts a dragged node back where the drag began;
 * a double-click returns it to its own place. */
function useNodeDrag(
  stageRef: RefObject<HTMLDivElement | null>,
  placement: Placement,
  setPlacement: Dispatch<SetStateAction<Placement>>,
  enabled: boolean,
  setOpen: Dispatch<SetStateAction<NodeId | null>>,
): { dragging: NodeId | null; dragHandlers: (id: NodeId, at: Point) => DragHandlers } {
  const [dragging, setDragging] = useState<NodeId | null>(null);
  const drag = useRef<Drag | null>(null);
  // When the last real drag ended: the click and double-click that follow a drag are
  // its tail, not a request to open the card or put the node back.
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

  useEffect(() => {
    if (!enabled && drag.current) end();
  }, [enabled, end]);

  const dragHandlers = (id: NodeId, at: Point): DragHandlers => {
    if (!enabled) return {};
    return {
      onPointerDown: (event) => {
        if (event.button !== 0 || drag.current) return;
        const box = stageRef.current?.getBoundingClientRect();
        if (!box) return;
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
          setOpen(null);
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
      onClick: () => {
        if (performance.now() - lastMove.current < 300) return;
        setOpen((current) => (current === id ? null : id));
      },
      onKeyDown: (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          setOpen((current) => (current === id ? null : id));
        }
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

interface LabelProps {
  layout: Layout;
  run: RunDetail;
  timeline: Timeline;
  states: Record<StepName, StepState>;
  at: number;
  dragging: NodeId | null;
  dragHandlers: (id: NodeId, at: Point) => DragHandlers;
  open: NodeId | null;
}

const NODE_NAMES: Record<NodeId, string> = {
  idea: "The idea",
  ...CRITIC_TITLES,
  combiner: "The verdict",
};

function NodeLabels({ layout: l, run, timeline, states, at, dragging, dragHandlers, open }: LabelProps) {
  const lineup = run.lineup === "frontier" ? "frontier models" : "local models";
  const combiner = timeline.steps.combiner;
  return (
    <div className="node-labels is-draggable">
      <Node
        id="idea"
        at={l.idea}
        state="done"
        dragging={dragging}
        open={open}
        handlers={dragHandlers("idea", l.idea)}
        room={Math.max(120, l.width - l.idea.x - 48)}
        compact={l.compact}
        title={run.idea_title}
        meta={`${countWord(4)} critics · ${lineup}`}
      />
      {/* A step's node appears when the step starts, and stays once it has answered. */}
      {CRITICS.filter((critic) => states[critic] !== "waiting").map((critic) => (
        <Node
          key={critic}
          id={critic}
          critic={critic}
          at={l.critics[critic]}
          state={states[critic]}
          dragging={dragging}
          open={open}
          handlers={dragHandlers(critic, l.critics[critic])}
          room={labelRoom(l, critic)}
          compact={l.compact}
          title={l.compact ? CRITIC_SHORT[critic] : CRITIC_TITLES[critic]}
          meta={criticMeta(timeline.steps[critic], states[critic], at, l.compact)}
        />
      ))}
      {states.combiner !== "waiting" && (
        <Node
          id="combiner"
          at={l.combiner}
          state={states.combiner}
          dragging={dragging}
          open={open}
          handlers={dragHandlers("combiner", l.combiner)}
          room={Math.max(120, l.width - l.combiner.x - 48)}
          title="Verdict"
          meta={
            l.compact
              ? verdictState(states.combiner, stepSeconds(combiner, at))
              : `${modelName(combiner.model)} · ${verdictState(states.combiner, stepSeconds(combiner, at))}`
          }
        />
      )}
    </div>
  );
}

function Node(props: {
  id: NodeId;
  critic?: CriticName;
  at: Point;
  state: StepState;
  dragging: NodeId | null;
  open: NodeId | null;
  handlers: DragHandlers;
  room: number;
  compact?: boolean;
  title: string;
  meta: string;
}) {
  const { id, critic, at, state, dragging, open, handlers, room, compact, title, meta } = props;
  const kind = id === "idea" ? "idea" : id === "combiner" ? "combiner" : "critic";
  return (
    <div
      {...handlers}
      className={`node node--${kind}${dragging === id ? " is-dragging" : ""}${open === id ? " is-open" : ""}`}
      data-critic={critic}
      data-state={state}
      role="button"
      tabIndex={0}
      aria-expanded={open === id}
      aria-controls={open === id ? `card-${id}` : undefined}
      aria-label={`${NODE_NAMES[id]}: ${meta}. Open details.`}
      title="Click for details. Drag to move."
      style={{ left: at.x, top: at.y, "--room": `${room}px` } as CSSProperties}
    >
      <span className="node-ring" data-state={state} />
      <div className={`node-text ${compact ? "node-text--below" : "node-text--right"}`} data-quiet>
        <span className="node-title">{title}</span>
        <span className="node-meta mono">{meta}</span>
      </div>
    </div>
  );
}

function criticMeta(step: Step, state: StepState, at: number, compact: boolean): string {
  const seconds = stepSeconds(step, at);
  if (state === "failed") return step.error === CUT_OFF ? CUT_OFF : "no usable answer";
  if (state === "working") return compact ? formatSeconds(seconds) : `${modelName(step.model)} · writing ${formatSeconds(seconds)}`;
  if (state === "done") {
    const rating = step.overall ?? "";
    return compact ? rating : `${modelName(step.model)} · ${rating} · ${formatSeconds(seconds)}`;
  }
  return modelName(step.model);
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

const CARD_WIDTH = 300;

/** A node's facts, opened by a click: the model and who runs it, what the step looks at,
 * how long it took, and once it has answered what it found and what it cost. */
function NodeCard(props: {
  id: NodeId;
  layout: Layout;
  run: RunDetail;
  timeline: Timeline;
  states: Record<StepName, StepState>;
  at: number;
  onClose: () => void;
}) {
  const { id, layout: l, run, timeline, states, at, onClose } = props;
  const anchor = id === "idea" ? l.idea : id === "combiner" ? l.combiner : l.critics[id];
  const width = Math.min(CARD_WIDTH, l.width - 32);
  // Beside the node on the side with room, kept inside the stage.
  const toLeft = anchor.x > l.width * 0.6;
  const left = Math.min(Math.max(toLeft ? anchor.x - 34 - width : anchor.x + 34, 16), l.width - width - 16);
  const below = anchor.y < l.height * 0.55;
  const style: CSSProperties = below
    ? { left, top: Math.min(anchor.y + 30, l.height - 120), width }
    : { left, bottom: Math.max(l.height - anchor.y + 30, 16), width };

  const rows: [string, string][] = [];
  let heading = "";
  let angle = "";
  let critic: CriticName | undefined;
  let topIssue: string | null = null;
  if (id === "idea") {
    heading = run.idea_title;
    angle = "The idea the four critics read, word for word.";
    rows.push(["Length", `${run.idea_text.length.toLocaleString("en-US")} characters`]);
    rows.push(["Models", run.lineup === "frontier" ? "frontier, hosted by four labs" : "local, on this computer"]);
  } else {
    const step = timeline.steps[id];
    const state = states[id];
    const seconds = stepSeconds(step, at);
    critic = id === "combiner" ? undefined : id;
    heading = id === "combiner" ? "Verdict" : CRITIC_TITLES[id];
    angle =
      id === "combiner"
        ? "Merges the four reviews into one statement, and shows where the critics disagree."
        : CRITIC_ANGLES[id];
    rows.push(["Model", `${modelName(step.model)}${step.model ? ` · ${modelHost(step.model)}` : ""}`]);
    rows.push([
      "State",
      state === "working"
        ? `writing, ${formatSeconds(seconds)}`
        : state === "done"
          ? `answered in ${formatSeconds(seconds)}`
          : state === "failed"
            ? step.error === CUT_OFF
              ? CUT_OFF
              : "gave no usable answer"
            : "waiting",
    ]);
    if (state === "done") {
      if (id === "combiner") {
        if (step.points !== null) rows.push(["Points", String(step.points)]);
      } else {
        if (step.overall) rows.push(["Rating", step.overall]);
        if (step.issues !== null) rows.push(["Issues", String(step.issues)]);
        topIssue = step.topIssue;
      }
      const tokens = stepTokens(step);
      if (tokens !== null && step.usage) {
        rows.push([
          "Tokens",
          `${formatTokens(tokens)} (${formatTokens(step.usage.input_tokens)} in, ${formatTokens(step.usage.output_tokens)} out)`,
        ]);
      }
      if (step.usage?.cost != null) rows.push(["Cost", formatCost(step.usage.cost)]);
    }
  }

  return (
    <div
      id={`card-${id}`}
      className="node-card"
      data-critic={critic}
      role="dialog"
      aria-label={`${heading}: details`}
      style={style}
      data-quiet
    >
      <div className="node-card-head">
        <p className="node-card-title">
          {critic && <span className="critic-dot" />}
          {heading}
        </p>
        <button type="button" className="node-card-close" onClick={onClose} aria-label="Close">
          <svg viewBox="0 0 12 12" width="12" height="12" aria-hidden="true">
            <path d="M2 2l8 8M10 2l-8 8" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
          </svg>
        </button>
      </div>
      <p className="node-card-angle">{angle}</p>
      <dl className="node-card-facts">
        {rows.map(([term, value]) => (
          <div key={term}>
            <dt>{term}</dt>
            <dd className="mono">{value}</dd>
          </div>
        ))}
      </dl>
      {topIssue && (
        <p className="node-card-top">
          <span>Most serious</span>
          {topIssue}
        </p>
      )}
    </div>
  );
}

function StatusLine(props: {
  id: string;
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
  if (error && run.status === "running") {
    return (
      <p className="status-line" role="alert">
        Lost contact with the panel's server ({error}). Trying again every few seconds.
      </p>
    );
  }

  const finished = run.status !== "running" && !replaying && !settling;
  const elapsed =
    timeline.start !== null ? Math.max(0, (Math.min(at, timeline.end ?? at) - timeline.start) / 1000) : null;
  const totals = runTotals(timeline, at);
  const step = currentStep(states);
  const writing = CRITICS.filter((critic) => states[critic] === "working");
  const cutOff = CRITICS.filter(
    (critic) => states[critic] === "failed" && timeline.steps[critic].error === CUT_OFF,
  );
  let what = settling
    ? "The run ended before the first critic started"
    : run.lineup === "local"
      ? "Starting the panel and loading the first model…"
      : "Starting the panel…";
  if (finished) {
    what =
      run.status === "success"
        ? "The panel has finished. The verdict is below."
        : run.status === "stopped"
          ? "The run was stopped."
          : "The run ended without a verdict.";
  } else if (writing.length > 1) {
    // Hosted critics run at the same time.
    const who = writing.length === CRITICS.length ? "all four" : listed(writing.map((c) => CRITIC_SHORT[c]));
    what = `${who.charAt(0).toUpperCase()}${who.slice(1)} critics are writing`;
  } else if (cutOff.length > 1) {
    const who = cutOff.length === CRITICS.length ? "all four" : listed(cutOff.map((c) => CRITIC_SHORT[c]));
    what = `${who.charAt(0).toUpperCase()}${who.slice(1)} critics were stopped before they answered`;
  } else if (step) {
    const state = states[step];
    const who = step === "combiner" ? "The combining step" : `${CRITIC_TITLES[step]} critic`;
    if (state === "failed" && timeline.steps[step].error === CUT_OFF) what = `${who} was ${CUT_OFF}`;
    else if (step === "combiner")
      what =
        state === "working"
          ? "Combining the four reviews"
          : state === "done"
            ? "The panel has finished"
            : `${who} gave no usable answer`;
    else what = `${who} ${state === "working" ? "is writing" : state === "failed" ? "gave no usable answer" : "is done"}`;
  }
  const label = replaying
    ? `Replay ${REPLAY_SPEED}×`
    : finished
      ? run.status === "success"
        ? "Finished"
        : run.status === "stopped"
          ? "Stopped"
          : "Ended"
      : settling
        ? "Finished"
        : "Running";

  return (
    <div className="status-line" ref={lineRef} tabIndex={-1}>
      <span className="mono status-label">{label}</span>
      <span className="mono">{formatSeconds(elapsed)}</span>
      {totals.tokens > 0 && (
        <span className="mono status-totals">
          {formatTokens(totals.tokens)} tokens{totals.cost !== null ? ` · ${formatCost(totals.cost)}` : ""}
        </span>
      )}
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

/** ["market", "risk", "customer"] -> "market, risk and customer". */
function listed(words: string[]): string {
  return words.length < 2 ? (words[0] ?? "") : `${words.slice(0, -1).join(", ")} and ${words.at(-1)}`;
}

/** Stopping throws away model time, so it takes a second, deliberate click. */
function StopControl({ onStop }: { onStop: () => Promise<void> }) {
  const [asking, setAsking] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const keepRef = useRef<HTMLButtonElement>(null);
  const stopRef = useRef<HTMLButtonElement>(null);
  const wasAsking = useRef(false);

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
    const timer = window.setTimeout(() => setAsking(false), 8000);
    return () => window.clearTimeout(timer);
  }, [asking]);

  const confirm = async () => {
    setAsking(false);
    setStopping(true);
    try {
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

/** The result below the stage: the run's summary beside the verdict. */
export function RunResult({ run, view, onReplay }: { run: RunDetail; view: RunView; onReplay: () => void }) {
  const record = run.record;
  const verdict = run.status === "success" && record?.combiner.combined ? record : null;
  return (
    <div className="run-result">
      <PanelSummary run={run} record={record} timeline={view.timeline} onReplay={record ? onReplay : undefined} />
      {verdict ? <Verdict record={verdict} runId={run.id} /> : <RunProblem run={run} />}
    </div>
  );
}

function PanelSummary(props: {
  run: RunDetail;
  record: PanelRecord | null;
  timeline: Timeline;
  onReplay?: () => void;
}) {
  const { run, record, timeline, onReplay } = props;
  const ref = useRevealGuard<HTMLElement>();
  const start = record?.started_at ?? null;
  const end = record?.finished_at ?? null;
  const seconds = start && end ? (Date.parse(end) - Date.parse(start)) / 1000 : null;
  const models = record
    ? new Set([...record.critics.map((c) => c.model), record.combiner.model].filter(Boolean)).size
    : 0;
  const totals = runTotals(timeline, Number.POSITIVE_INFINITY);
  return (
    <aside ref={ref} className="panel-summary" aria-label="The run">
      <h2 className="panel-idea">{run.idea_title}</h2>
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
      <dl className="panel-figures">
        {seconds !== null && (
          <div>
            <dt>Time</dt>
            <dd className="mono">{formatSeconds(seconds)}</dd>
          </div>
        )}
        {models > 0 && (
          <div>
            <dt>Models</dt>
            <dd className="mono">
              {models} {run.lineup}
            </dd>
          </div>
        )}
        {totals.tokens > 0 && (
          <div>
            <dt>Tokens</dt>
            <dd className="mono">{formatTokens(totals.tokens)}</dd>
          </div>
        )}
        {totals.cost !== null && (
          <div>
            <dt>Cost</dt>
            <dd className="mono">{formatCost(totals.cost)}</dd>
          </div>
        )}
      </dl>
      <p className="panel-meta">Every model call is logged.</p>
      <div className="panel-actions">
        <a href="#issues">Each critic's issues</a>
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
      : (run.error ?? run.record?.error ?? "The panel did not finish.");
  return (
    <section className="verdict verdict--problem" aria-live="polite">
      <p className="verdict-lede">No verdict for this run.</p>
      <p className="verdict-rest">{message}</p>
      {run.record && (
        <p className="verdict-rest">
          <a href="#issues">See what each critic said</a>
        </p>
      )}
    </section>
  );
}
