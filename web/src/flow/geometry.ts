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

export interface Layout {
  width: number;
  height: number;
  idea: Point;
  critics: Record<CriticName, Point>;
  combiner: Point;
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

/** Margins a dragged node keeps from the stage's edges, with room for its label. */
const EDGE = { side: 28, top: 40, bottom: 72 };

/** A point kept inside the stage's margins. */
export function clampToStage(width: number, height: number, point: Point): Point {
  return {
    x: Math.min(Math.max(point.x, EDGE.side), Math.max(EDGE.side, width - EDGE.side)),
    y: Math.min(Math.max(point.y, EDGE.top), Math.max(EDGE.top, height - EDGE.bottom)),
  };
}

/** Stream ids: the idea flowing to a critic, and a critic flowing to the combiner. */
export type StreamId = `in:${CriticName}` | `out:${CriticName}`;

export const STREAMS: StreamId[] = [
  ...CRITICS.map((c) => `in:${c}` as const),
  ...CRITICS.map((c) => `out:${c}` as const),
];

/** The panel top to bottom: the idea above, the four critics in a row, the verdict
 * below, as the page itself reads. */
export function layout(width: number, height: number, placement: Placement = {}): Layout {
  const compact = width < 720;
  const left = width * (compact ? 0.13 : 0.16);
  const right = width * (compact ? 0.87 : 0.84);
  const row = height * 0.5;
  const critics = Object.fromEntries(
    CRITICS.map((critic, i) => [critic, { x: left + ((right - left) * i) / (CRITICS.length - 1), y: row }]),
  ) as Record<CriticName, Point>;
  const placed = (id: NodeId, slot: Point): Point => {
    const moved = placement[id];
    return moved ? clampToStage(width, height, { x: moved.fx * width, y: moved.fy * height }) : slot;
  };
  return {
    width,
    height,
    idea: placed("idea", { x: width / 2, y: height * 0.14 }),
    critics: Object.fromEntries(
      CRITICS.map((critic) => [critic, placed(critic, critics[critic])]),
    ) as Record<CriticName, Point>,
    combiner: placed("combiner", { x: width / 2, y: height * 0.84 }),
    spread: Math.max(8, Math.min(width * 0.022, compact ? 14 : 30)),
    compact,
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

/** The curve a stream follows. Streams fall from the idea and arrive at their critic
 * from above; streams to the verdict leave downwards and gather into it. */
export function curve(l: Layout, stream: StreamId): Curve {
  const [direction, critic] = stream.split(":") as ["in" | "out", CriticName];
  const node = l.critics[critic];
  if (direction === "in") {
    const dx = node.x - l.idea.x;
    const dy = node.y - l.idea.y;
    return {
      p0: l.idea,
      p1: { x: l.idea.x + dx * 0.12, y: l.idea.y + dy * 0.62 },
      p2: { x: node.x, y: node.y - dy * 0.5 },
      p3: node,
    };
  }
  const dx = l.combiner.x - node.x;
  const dy = l.combiner.y - node.y;
  return {
    p0: node,
    p1: { x: node.x, y: node.y + dy * 0.5 },
    p2: { x: l.combiner.x - dx * 0.12, y: l.combiner.y - dy * 0.62 },
    p3: l.combiner,
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
