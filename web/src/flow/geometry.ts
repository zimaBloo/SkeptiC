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
  /** Narrow screens: critics further left, the verdict's label above its node. */
  compact: boolean;
}

/** Stream ids: the idea flowing to a critic, and a critic flowing to the combiner. */
export type StreamId = `in:${CriticName}` | `out:${CriticName}`;

export const STREAMS: StreamId[] = [
  ...CRITICS.map((c) => `in:${c}` as const),
  ...CRITICS.map((c) => `out:${c}` as const),
];

export function layout(width: number, height: number): Layout {
  const compact = width < 720;
  const centre = height * 0.52;
  const column = Math.min(height * 0.62, 520);
  const critics = Object.fromEntries(
    CRITICS.map((critic, i) => [
      critic,
      {
        x: width * (compact ? 0.4 : 0.56),
        y: centre - column / 2 + (column * i) / (CRITICS.length - 1),
      },
    ]),
  ) as Record<CriticName, Point>;
  return {
    width,
    height,
    idea: { x: width * (compact ? 0.09 : 0.13), y: centre },
    critics,
    combiner: { x: width * (compact ? 0.92 : 0.85), y: centre },
    spread: Math.max(8, Math.min(height * 0.045, compact ? 20 : 34)),
    compact,
  };
}

/** Room in px for a critic's label before it would reach the verdict's node. */
export function labelRoom(l: Layout, critic: CriticName): number {
  return Math.max(60, l.combiner.x - l.critics[critic].x - 48);
}

/** Which side of its outgoing stream a critic's label sits on. The stream leaves the
 * critic level and then bends towards the verdict, so the label takes the other side,
 * as a map sets a name beside a road rather than on it. */
export function labelSide(l: Layout, critic: CriticName): "above" | "below" {
  return l.critics[critic].y <= l.combiner.y ? "above" : "below";
}

/** The curve a stream follows. Streams from the idea bow past their critic and
 * arrive from beyond it, as in the sketch; streams to the combiner arrive level. */
export function curve(l: Layout, stream: StreamId): Curve {
  const [direction, critic] = stream.split(":") as ["in" | "out", CriticName];
  const node = l.critics[critic];
  if (direction === "in") {
    const dx = node.x - l.idea.x;
    const dy = node.y - l.idea.y;
    return {
      p0: l.idea,
      p1: { x: l.idea.x + dx * 0.42, y: l.idea.y + dy * 0.05 },
      p2: { x: node.x - dx * 0.4, y: node.y + dy * 0.42 },
      p3: node,
    };
  }
  const dx = l.combiner.x - node.x;
  return {
    p0: node,
    p1: { x: node.x + dx * 0.46, y: node.y },
    p2: { x: l.combiner.x - dx * 0.5, y: l.combiner.y },
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
