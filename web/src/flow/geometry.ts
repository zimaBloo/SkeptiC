// Where the run's steps sit on screen and the curves the flow follows between them.
// One layout serves both the canvas and the HTML labels laid over it.

import { CRITICS, type CriticName } from "../api";

export interface Point {
  x: number;
  y: number;
}

export interface Curve {
  p0: Point;
  p1: Point;
  p2: Point;
  p3: Point;
}

/** A capsule's centre and size: the idea's inlet at the top of the stage, the verdict's
 * outlet near the bottom. */
export interface Capsule extends Point {
  width: number;
  height: number;
}

export interface Layout {
  width: number;
  height: number;
  /** The centre of the idea's inlet. */
  idea: Point;
  critics: Record<CriticName, Point>;
  /** The centre of the verdict's outlet. */
  combiner: Point;
  inlet: Capsule;
  outlet: Capsule;
  /** The height of the critics' row before any is dragged. */
  row: number;
  /** Half the width of a stream at its widest, in px. */
  spread: number;
  /** Narrow screens: the critics sit close together, so their labels go below them. */
  compact: boolean;
}

/** The nodes on the run page: the idea, each critic, and the verdict. */
export type NodeId = "idea" | CriticName | "combiner";

/** Nodes the viewer has dragged somewhere else, as fractions of the stage's width and
 * height, so an arrangement keeps its shape when the window is resized. */
export type Placement = Partial<Record<NodeId, { fx: number; fy: number }>>;

/** The inlet and outlet: their height, and their distance from the stage's edges. The
 * outlet leaves room below it for the status line, which wraps to three lines on a phone. */
export const CAPSULE_HEIGHT = 56;
const INLET_TOP = 28;
const outletBottom = (width: number) => (isCompact(width) ? 140 : 76);

/** Narrow screens: the critics sit close together, so their labels go below them. */
const isCompact = (width: number) => width < 720;

/** Margins a dragged critic keeps from the stage's edges: clear of the inlet above and the
 * outlet below, with room for its label. */
function edges(width: number) {
  return { side: 28, top: INLET_TOP + CAPSULE_HEIGHT + 56, bottom: outletBottom(width) + CAPSULE_HEIGHT + 50 };
}

/** A point kept inside the stage's margins. */
export function clampToStage(width: number, height: number, point: Point): Point {
  const edge = edges(width);
  return {
    x: Math.min(Math.max(point.x, edge.side), Math.max(edge.side, width - edge.side)),
    y: Math.min(Math.max(point.y, edge.top), Math.max(edge.top, height - edge.bottom)),
  };
}

/** Stream ids: the idea flowing to a critic, and a critic flowing to the combiner. */
export type StreamId = `in:${CriticName}` | `out:${CriticName}`;

export const STREAMS: StreamId[] = [
  ...CRITICS.map((c) => `in:${c}` as const),
  ...CRITICS.map((c) => `out:${c}` as const),
];

/** The panel top to bottom: the idea's inlet across the top, the four critics in a row,
 * the verdict's outlet near the bottom, as the page itself reads. Only critics move when
 * dragged: the inlet and the outlet are where the flow enters and leaves. */
export function layout(width: number, height: number, placement: Placement = {}): Layout {
  const compact = isCompact(width);
  const capsuleWidth = compact ? Math.max(200, width - 32) : Math.min(560, Math.max(320, width * 0.44));
  const inlet = { x: width / 2, y: INLET_TOP + CAPSULE_HEIGHT / 2, width: capsuleWidth, height: CAPSULE_HEIGHT };
  const outlet = {
    x: width / 2,
    y: height - outletBottom(width) - CAPSULE_HEIGHT / 2,
    width: capsuleWidth,
    height: CAPSULE_HEIGHT,
  };
  const left = width * (compact ? 0.13 : 0.16);
  const right = width * (compact ? 0.87 : 0.84);
  const row = (inlet.y + CAPSULE_HEIGHT / 2 + outlet.y - CAPSULE_HEIGHT / 2) / 2;
  const critics = Object.fromEntries(
    CRITICS.map((critic, i) => [critic, { x: left + ((right - left) * i) / (CRITICS.length - 1), y: row }]),
  ) as Record<CriticName, Point>;
  const placed = (id: CriticName, slot: Point): Point => {
    const moved = placement[id];
    return moved ? clampToStage(width, height, { x: moved.fx * width, y: moved.fy * height }) : slot;
  };
  return {
    width,
    height,
    idea: { x: inlet.x, y: inlet.y },
    critics: Object.fromEntries(
      CRITICS.map((critic) => [critic, placed(critic, critics[critic])]),
    ) as Record<CriticName, Point>,
    combiner: { x: outlet.x, y: outlet.y },
    inlet,
    outlet,
    row,
    spread: Math.max(8, Math.min(width * 0.022, compact ? 14 : 30)),
    compact,
  };
}

/** Where a critic's streams leave the inlet and reach the outlet: spread along the
 * capsule's edge in the critics' order, so the flow pours out of the idea and into the
 * verdict rather than out of and into one point. */
export function port(l: Layout, critic: CriticName, end: "inlet" | "outlet"): Point {
  const capsule = end === "inlet" ? l.inlet : l.outlet;
  const i = CRITICS.indexOf(critic);
  const offset = ((i - (CRITICS.length - 1) / 2) / ((CRITICS.length - 1) / 2)) * capsule.width * 0.3;
  return {
    x: capsule.x + offset,
    y: end === "inlet" ? capsule.y + capsule.height / 2 : capsule.y - capsule.height / 2,
  };
}

/** Room in px for a critic's label, which sits to the right of its node: up to the next
 * critic in the row, or to the stage's edge for the last one. */
export function labelRoom(l: Layout, critic: CriticName): number {
  const { x, y } = l.critics[critic];
  const next = CRITICS.map((c) => l.critics[c])
    .filter((p) => p.x > x + 4 && Math.abs(p.y - y) < 90)
    .reduce((nearest, p) => Math.min(nearest, p.x), l.width - 12);
  return Math.max(60, next - x - 44);
}

/** Which side of its node a critic's label takes on a phone, where the critics are too
 * close together for labels beside them. */
export function labelSide(l: Layout, critic: CriticName): "above" | "below" {
  return l.critics[critic].y <= l.combiner.y ? "below" : "above";
}

/** The curve a stream follows. Streams fall from the inlet's lower edge and arrive at
 * their critic from above; streams to the verdict leave downwards and pour into the
 * outlet's upper edge. */
export function curve(l: Layout, stream: StreamId): Curve {
  const [direction, critic] = stream.split(":") as ["in" | "out", CriticName];
  const node = l.critics[critic];
  if (direction === "in") {
    const start = port(l, critic, "inlet");
    const dy = node.y - start.y;
    return {
      p0: start,
      p1: { x: start.x, y: start.y + dy * 0.5 },
      p2: { x: node.x, y: node.y - dy * 0.5 },
      p3: node,
    };
  }
  const end = port(l, critic, "outlet");
  const dy = end.y - node.y;
  return {
    p0: node,
    p1: { x: node.x, y: node.y + dy * 0.5 },
    p2: { x: end.x, y: end.y - dy * 0.5 },
    p3: end,
  };
}

export function pointAt(c: Curve, t: number): Point {
  const u = 1 - t;
  const a = u * u * u;
  const b = 3 * u * u * t;
  const d = 3 * u * t * t;
  const e = t * t * t;
  return {
    x: a * c.p0.x + b * c.p1.x + d * c.p2.x + e * c.p3.x,
    y: a * c.p0.y + b * c.p1.y + d * c.p2.y + e * c.p3.y,
  };
}

/** The unit normal of a curve at t (perpendicular to its direction of travel). */
export function normalAt(c: Curve, t: number): Point {
  const u = 1 - t;
  const x = 3 * u * u * (c.p1.x - c.p0.x) + 6 * u * t * (c.p2.x - c.p1.x) + 3 * t * t * (c.p3.x - c.p2.x);
  const y = 3 * u * u * (c.p1.y - c.p0.y) + 6 * u * t * (c.p2.y - c.p1.y) + 3 * t * t * (c.p3.y - c.p2.y);
  const length = Math.hypot(x, y) || 1;
  return { x: -y / length, y: x / length };
}

/** Approximate length of a curve in px. */
export function curveLength(c: Curve, steps = 24): number {
  let length = 0;
  let previous = c.p0;
  for (let i = 1; i <= steps; i++) {
    const next = pointAt(c, i / steps);
    length += Math.hypot(next.x - previous.x, next.y - previous.y);
    previous = next;
  }
  return length;
}

/** How wide a stream is at t, from 0 (at a node) to 1 (widest). */
export function bundleWidth(t: number): number {
  return Math.pow(Math.sin(Math.PI * t), 1.15);
}
