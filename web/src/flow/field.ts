// The flow field: thin streaks drawn along each stream's curve with fading
// trails, in the manner of the Wind Map and earth.nullschool. A stream's
// intensity (0 to 1) sets how many streaks it carries and how fast they move.
// The pointer pushes nearby streaks aside; they drift back to their path.
// Each frame is drawn afresh from every streak's last few positions, rather than by
// fading the previous frame: a faded canvas keeps a grey ghost of old trails, and a
// moved node would leave its streams and cloud behind as skid marks.

import { CRITICS, type CriticName } from "../api";
import {
  STREAMS,
  bundleWidth,
  curve,
  curveLength,
  layout,
  normalAt,
  pointAt,
  type Curve,
  type Layout,
  type Placement,
  type StreamId,
} from "./geometry";

export type Intensities = Partial<Record<StreamId, number>>;

/** Nodes that gather a cloud when their step is done: each critic and the verdict. */
export type CloudNode = CriticName | "combiner";
/** How many specks each node's cloud holds (0 for none). */
export type Clouds = Partial<Record<CloudNode, number>>;

const CLOUD_NODES: CloudNode[] = [...CRITICS, "combiner"];

/** A rectangle in CSS px, relative to the canvas. */
export interface Rect {
  x: number;
  y: number;
  width: number;
  height: number;
}
export type StreamColours = Record<StreamId, string>;

/** A streak's recent positions: a ring of TRAIL_POINTS entries of x, y and glow (its
 * brightness there, 0 to 1), newest at `head`, `size` of them filled. */
interface Trail {
  trail: Float32Array;
  head: number;
  size: number;
}

interface Particle extends Trail {
  stream: number; // index into STREAMS, or DORMANT while no stream has work
  t: number;
  speed: number; // px per second along the curve at full intensity
  offset: number; // -1..1 across the stream
  wobble: number;
  phase: number;
  dx: number; // displacement from the pointer
  dy: number;
  nx: number; // where it is now
  ny: number;
  fresh: boolean;
  /** Frames since the streak retired, 0 while it flows: a retired streak stops and its
   * tail fades out before it starts again, rather than vanishing at once. */
  drain: number;
}

/** A streak that has arrived at a finished step and now circles its node. */
interface CloudParticle extends Trail {
  node: number; // index into CLOUD_NODES
  angle: number;
  /** Where in the cloud it settles, 0 (inner edge) to 1 (outer edge). */
  depth: number;
  /** How far out it has drifted so far, 0 to 1: arrivals start at the node and spread. */
  reach: number;
  spin: number; // radians per second
  phase: number;
  nx: number;
  ny: number;
  fresh: boolean;
}

export interface FieldOptions {
  colours: StreamColours;
  /** Streaks alive at once. Kept modest: the same machine runs the models. */
  count?: number;
}

const POINTER_RADIUS = 110;
const POINTER_PUSH = 900;
const SPRING_BACK = 2.6; // per second
const INTENSITY_EASE = 1.6; // per second
const IDLE_FLOOR = 0.035; // calm streams still drift
const DORMANT = -1; // a streak waiting for a stream with work
// Waiting streaks join streams with work at a steady rate: the whole pool over about
// one trip along a stream, so a starting stream fills evenly instead of in a slug.
const JOIN_SECONDS = 3.5;
// A tail is a streak's last TRAIL_POINTS positions, about a third of a second, dimming
// towards its end; AGE is the brightness of each step back. Tails are drawn in LEVELS
// steps of brightness, one stroke per stream and step.
const TRAIL_POINTS = 20;
const AGE = Float32Array.from({ length: TRAIL_POINTS }, (_, j) => Math.pow(1 - j / TRAIL_POINTS, 1.6));
const LEVELS = 16;
// The most specks a cloud holds. A cloud spreads with the square root of its specks, so
// a bigger one is as dense as a smaller one, only wider.
const CLOUD_MAX = 300;
// Radius of a cloud's inner edge and how far a full cloud reaches past it, in px.
const CLOUD_INNER = 26;
const CLOUD_REACH = 54;

export class FlowField {
  private readonly ctx: CanvasRenderingContext2D;
  private readonly colours: string[];
  private readonly particles: Particle[] = [];
  private layout: Layout = layout(1, 1);
  private placement: Placement = {};
  private curves: Curve[] = [];
  private lengths: number[] = [];
  private dpr = 1;
  private target = new Float32Array(STREAMS.length);
  private current = new Float32Array(STREAMS.length);
  private pointer: { x: number; y: number } | null = null;
  private quiet: Rect[] = [];
  private readonly segments: Segments[] = Array.from({ length: STREAMS.length * LEVELS }, newSegments);
  private readonly cloudSegments: Segments[] = Array.from({ length: CLOUD_NODES.length * LEVELS }, newSegments);
  private frame = 0;
  private joinBudget = 0;
  private last = 0;
  private running = false;
  private readonly cloud: CloudParticle[] = [];
  /** How far each cloud spreads, 0 to 1, from its capacity. */
  private cloudSize = new Float32Array(CLOUD_NODES.length);
  private cloudCapacity = new Int32Array(CLOUD_NODES.length);
  private cloudCount = new Int32Array(CLOUD_NODES.length);
  private readonly cloudColours: string[];

  constructor(
    private readonly canvas: HTMLCanvasElement,
    options: FieldOptions,
  ) {
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("canvas 2d context unavailable");
    this.ctx = ctx;
    this.colours = STREAMS.map((id) => options.colours[id]);
    // A critic's cloud takes the colour of the stream that fed it; the verdict's, the
    // colour every outgoing stream ends in.
    this.cloudColours = CLOUD_NODES.map((node) =>
      node === "combiner" ? options.colours["out:market"] : options.colours[`in:${node}`],
    );
    const count = options.count ?? 1250;
    for (let i = 0; i < count; i++) this.particles.push(this.spawn(true));
  }

  resize(width: number, height: number, dpr: number): void {
    this.dpr = dpr;
    this.canvas.width = Math.round(width * dpr);
    this.canvas.height = Math.round(height * dpr);
    this.layout = layout(width, height, this.placement);
    this.curves = STREAMS.map((id) => curve(this.layout, id));
    this.lengths = this.curves.map((c) => curveLength(c));
    for (const p of this.particles) p.fresh = true;
  }

  setIntensities(intensities: Intensities, immediate = false): void {
    STREAMS.forEach((id, i) => {
      this.target[i] = intensities[id] ?? 0;
      if (immediate) this.current[i] = this.target[i] ?? 0;
    });
    if (immediate) {
      // Start already flowing: spread every streak along the streams that have work.
      for (let k = 0; k < this.particles.length; k++) this.particles[k] = this.spawn(true);
    }
  }

  /** Move nodes the viewer has dragged; streams follow at once and the streaks on them
   * carry on along the new paths. */
  setPlacement(placement: Placement): void {
    const before = this.curves;
    const beforeNodes = CLOUD_NODES.map((_, node) => ({ ...this.nodePoint(node) }));
    this.placement = placement;
    this.layout = layout(this.layout.width, this.layout.height, placement);
    this.curves = STREAMS.map((id) => curve(this.layout, id));
    this.lengths = this.curves.map((c) => curveLength(c));
    // A node that jumps (put back, reset, a fast flick) would draw every streak on its
    // streams as a long straight line from the old place; those streaks start afresh.
    const jumped = new Set<number>();
    this.curves.forEach((c, i) => {
      const old = before[i];
      if (!old) return;
      const moved = (a: { x: number; y: number }, b: { x: number; y: number }) => Math.hypot(a.x - b.x, a.y - b.y) > 24;
      if (moved(old.p0, c.p0) || moved(old.p3, c.p3)) jumped.add(i);
    });
    // Streaks on a stream that only shifted carry their tails along with it, so a
    // dragged node leaves no smear behind.
    for (const p of this.particles) {
      if (p.stream === DORMANT || p.size === 0) continue;
      if (jumped.has(p.stream)) {
        p.fresh = true;
        continue;
      }
      const old = before[p.stream];
      const now = this.curves[p.stream];
      if (!old || !now || old === now) continue;
      const from = this.streamPoint(p, old);
      const to = this.streamPoint(p, now);
      if (from.x !== to.x || from.y !== to.y) shiftTrail(p, to.x - from.x, to.y - from.y);
    }
    // A cloud moves with its node, tails and all.
    const shifts = beforeNodes.map((old, node) => {
      const now = this.nodePoint(node);
      return { x: now.x - old.x, y: now.y - old.y };
    });
    for (const c of this.cloud) {
      const d = shifts[c.node];
      if (d && (d.x !== 0 || d.y !== 0)) shiftTrail(c, d.x, d.y);
    }
  }

  /** Where a streak sits on a stream's curve, before the pointer's push. */
  private streamPoint(p: Particle, c: Curve): { x: number; y: number } {
    const base = pointAt(c, p.t);
    const n = normalAt(c, p.t);
    const across = (p.offset + p.wobble * Math.sin(p.phase + p.t * 9)) * bundleWidth(p.t) * this.layout.spread;
    return { x: base.x + n.x * across, y: base.y + n.y * across };
  }

  /** Set how many specks each finished step's cloud holds. Streaks that reach a finished
   * node join its cloud until it is full; with `immediate` (a finished run opened later)
   * the clouds are filled at once. 0 empties that node's cloud. */
  setClouds(clouds: Clouds, immediate = false): void {
    CLOUD_NODES.forEach((node, i) => {
      const capacity = Math.min(CLOUD_MAX, Math.max(0, Math.round(clouds[node] ?? 0)));
      this.cloudCapacity[i] = capacity;
      this.cloudSize[i] = Math.sqrt(capacity / CLOUD_MAX);
      if (capacity === 0 && (this.cloudCount[i] ?? 0) > 0) {
        for (let k = this.cloud.length - 1; k >= 0; k--) if (this.cloud[k]?.node === i) this.cloud.splice(k, 1);
        this.cloudCount[i] = 0;
      }
      if (immediate) {
        while ((this.cloudCount[i] ?? 0) < capacity) this.addCloud(i, true);
      }
    });
  }

  private addCloud(node: number, settled: boolean): void {
    this.cloud.push({
      node,
      angle: Math.random() * Math.PI * 2,
      depth: Math.pow(Math.random(), 0.7),
      reach: settled ? 1 : 0,
      spin: (0.18 + Math.random() * 0.32) * (node % 2 === 0 ? 1 : -1),
      phase: Math.random() * Math.PI * 2,
      nx: 0,
      ny: 0,
      fresh: true,
      ...newTrail(),
    });
    this.cloudCount[node] = (this.cloudCount[node] ?? 0) + 1;
  }

  /** The node a stream ends at, as an index into CLOUD_NODES. */
  private endNode(stream: number): number {
    const id = STREAMS[stream] ?? "";
    return id.startsWith("in:") ? CLOUD_NODES.indexOf(id.slice(3) as CriticName) : CLOUD_NODES.indexOf("combiner");
  }

  private nodePoint(node: number): { x: number; y: number } {
    const id = CLOUD_NODES[node];
    return id === "combiner" || id === undefined ? this.layout.combiner : this.layout.critics[id];
  }

  setPointer(point: { x: number; y: number } | null): void {
    this.pointer = point;
  }

  /** Areas the streaks leave clear, as a map leaves its line work clear under labels. */
  setQuietZones(zones: Rect[]): void {
    this.quiet = zones;
  }

  private inQuietZone(x: number, y: number): boolean {
    for (const z of this.quiet) {
      if (x >= z.x && x <= z.x + z.width && y >= z.y && y <= z.y + z.height) return true;
    }
    return false;
  }

  start(): void {
    if (this.running) return;
    this.running = true;
    this.last = performance.now();
    const tick = (now: number) => {
      if (!this.running) return;
      const dt = Math.min(0.05, (now - this.last) / 1000);
      // Hold to about 60 Hz on high refresh screens; the effect needs no more.
      if (dt >= 1 / 64) {
        this.last = now;
        this.step(dt);
        this.draw();
      }
      this.frame = requestAnimationFrame(tick);
    };
    this.frame = requestAnimationFrame(tick);
  }

  stop(): void {
    this.running = false;
    cancelAnimationFrame(this.frame);
  }

  /** A still picture of the streams, for reduced motion: faint hairlines. */
  drawStill(): void {
    const { ctx, dpr } = this;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const samples = 120;
    STREAMS.forEach((_, i) => {
      const c = this.curves[i];
      const strength = Math.max(this.target[i] ?? 0, IDLE_FLOOR * 3);
      // Only streams with work are drawn, as in the moving field.
      if (!c || (this.target[i] ?? 0) === 0) return;
      for (const offset of [-0.6, -0.2, 0.2, 0.6]) {
        ctx.beginPath();
        // Lift the pen inside label clearings, as the moving streaks do.
        let pen = false;
        for (let s = 0; s <= samples; s++) {
          const t = s / samples;
          const p = pointAt(c, t);
          const n = normalAt(c, t);
          const w = offset * bundleWidth(t) * this.layout.spread;
          const x = p.x + n.x * w;
          const y = p.y + n.y * w;
          if (this.inQuietZone(x, y)) {
            pen = false;
            continue;
          }
          if (pen) ctx.lineTo(x, y);
          else ctx.moveTo(x, y);
          pen = true;
        }
        ctx.strokeStyle = this.colours[i] ?? "#fff";
        ctx.globalAlpha = 0.18 + 0.5 * strength;
        ctx.lineWidth = 1;
        ctx.stroke();
      }
    });
    // Clouds as still rings of dots, one per speck, their size the same as in motion.
    CLOUD_NODES.forEach((_, node) => {
      const size = this.cloudSize[node] ?? 0;
      if (size === 0) return;
      const centre = this.nodePoint(node);
      ctx.fillStyle = this.cloudColours[node] ?? "#fff";
      ctx.globalAlpha = 0.5;
      const dots = this.cloudCapacity[node] ?? 0;
      for (let d = 0; d < dots; d++) {
        const angle = (d / dots) * Math.PI * 2;
        const radius = CLOUD_INNER + CLOUD_REACH * size * (0.4 + 0.6 * ((d * 7) % 10) / 10);
        const x = centre.x + Math.cos(angle) * radius;
        const y = centre.y + Math.sin(angle) * radius * 0.86;
        if (this.inQuietZone(x, y)) continue;
        ctx.fillRect(x - 0.6, y - 0.6, 1.2, 1.2);
      }
    });
    ctx.globalAlpha = 1;
  }

  private spawn(scatter: boolean): Particle {
    const stream = this.pickStream();
    const particle: Particle = {
      stream,
      t: scatter ? Math.random() : Math.random() * 0.04,
      speed: 150 + Math.random() * 130,
      offset: gaussian() * 0.5,
      wobble: (Math.random() - 0.5) * 0.35,
      phase: Math.random() * Math.PI * 2,
      dx: 0,
      dy: 0,
      nx: 0,
      ny: 0,
      fresh: true,
      drain: 0,
      ...newTrail(),
    };
    // A streak leaving a finished critic for the verdict starts in that critic's cloud,
    // where the work gathered, and eases onto its stream as it goes (the same spring
    // that brings a streak back after the pointer pushed it aside). The cloud keeps its
    // size: it still shows what the step used.
    if (!scatter && stream !== DORMANT) {
      const source = this.startNode(stream);
      const from = source >= 0 ? this.randomCloud(source) : null;
      if (from) {
        const origin = this.nodePoint(source);
        particle.dx = from.nx - origin.x;
        particle.dy = from.ny - origin.y;
        particle.t = 0;
      }
    }
    return particle;
  }

  /** The finished node an outgoing stream starts at, as an index into CLOUD_NODES. */
  private startNode(stream: number): number {
    const id = STREAMS[stream] ?? "";
    return id.startsWith("out:") ? CLOUD_NODES.indexOf(id.slice(4) as CriticName) : -1;
  }

  private randomCloud(node: number): CloudParticle | null {
    const count = this.cloudCount[node] ?? 0;
    if (count === 0) return null;
    // Cloud particles are kept in one list; sample a few and take one of this node's.
    for (let tries = 0; tries < 12; tries++) {
      const c = this.cloud[Math.floor(Math.random() * this.cloud.length)];
      if (c && c.node === node && !c.fresh) return c;
    }
    return null;
  }

  /** A stream for a new streak, weighted by how strongly each flows. A stream with no
   * work (target 0) gets none, so nothing flows to a step that is not running. */
  private pickStream(): number {
    let total = 0;
    const weights = Array.from(this.current, (v, i) => {
      const w = (this.target[i] ?? 0) > 0 ? Math.pow(Math.max(v, IDLE_FLOOR), 1.3) : 0;
      total += w;
      return w;
    });
    if (total === 0) return DORMANT;
    let r = Math.random() * total;
    for (let i = 0; i < weights.length; i++) {
      r -= weights[i] ?? 0;
      if (r <= 0 && (weights[i] ?? 0) > 0) return i;
    }
    return weights.findLastIndex((w) => w > 0);
  }

  private step(dt: number): void {
    const ease = 1 - Math.exp(-dt * INTENSITY_EASE);
    for (let i = 0; i < this.current.length; i++) {
      const c = this.current[i] ?? 0;
      this.current[i] = c + ((this.target[i] ?? 0) - c) * ease;
    }
    const spring = Math.exp(-dt * SPRING_BACK);
    const pointer = this.pointer;
    const joinRate = this.particles.length / JOIN_SECONDS;
    const hasWork = this.target.some((v) => v > 0);
    // No backlog builds up while nothing has work, so a starting stream gets no burst.
    this.joinBudget = hasWork ? Math.min(this.joinBudget + joinRate * dt, joinRate * 0.1) : 0;
    for (let k = 0; k < this.particles.length; k++) {
      let p = this.particles[k] as Particle;
      if (p.drain > 0) {
        // A retired streak waits for its tail to fade, then starts again.
        if (++p.drain < TRAIL_POINTS) continue;
        p = this.spawn(false);
        this.particles[k] = p;
      }
      if (p.stream === DORMANT) {
        // Waiting streaks join a stream with work from its source, at a steady rate.
        if (this.joinBudget < 1) continue;
        this.joinBudget -= 1;
        p = this.spawn(false);
        this.particles[k] = p;
        if (p.stream === DORMANT) continue;
      }
      const target = this.target[p.stream] ?? 0;
      const stopping = target === 0;
      // Pace and lifetime follow where the stream is heading, so a stream that has just
      // started moves at its full pace at once; brightness (in draw) eases in instead.
      const intensity = Math.max(this.current[p.stream] ?? 0, target, IDLE_FLOOR);
      // On a calm stream that still has work, streaks retire early so a busier one
      // fills fast. On a stream whose step has ended, streaks already under way
      // finish their path at full speed instead of vanishing mid-air.
      if (!stopping && Math.random() < (1 - intensity) * dt * 0.9) {
        p.drain = 1;
        continue;
      }
      const length = this.lengths[p.stream] ?? 1;
      p.t += (p.speed * dt * (stopping ? 1 : 0.25 + 0.75 * intensity)) / length;
      if (p.t >= 1) {
        // A streak that reaches a finished step stays there, in its cloud.
        const node = this.endNode(p.stream);
        if (stopping && node >= 0 && (this.cloudCount[node] ?? 0) < (this.cloudCapacity[node] ?? 0)) {
          this.addCloud(node, false);
        }
        p.drain = 1;
        continue;
      }
      const c = this.curves[p.stream];
      if (!c) continue;
      const { x: bx, y: by } = this.streamPoint(p, c);
      p.dx *= spring;
      p.dy *= spring;
      if (pointer) {
        const ox = bx + p.dx - pointer.x;
        const oy = by + p.dy - pointer.y;
        const dist = Math.hypot(ox, oy);
        if (dist < POINTER_RADIUS && dist > 0.001) {
          const push = Math.pow(1 - dist / POINTER_RADIUS, 2) * POINTER_PUSH * dt;
          p.dx += (ox / dist) * push;
          p.dy += (oy / dist) * push;
        }
      }
      const x = bx + p.dx;
      const y = by + p.dy;
      if (p.fresh) {
        p.size = 0;
        p.fresh = false;
      }
      // Fainter near the nodes, where a streak leaves and arrives.
      const edge = Math.min(1, p.t / 0.07, (1 - p.t) / 0.07);
      record(p, x, y, Math.max(0.25, edge));
      p.nx = x;
      p.ny = y;
    }
    this.stepClouds(dt);
  }

  private stepClouds(dt: number): void {
    const time = performance.now() / 1000;
    // Streaks still on their way fill most of a cloud; a cloud whose stream had few
    // left tops itself up slowly from the node.
    CLOUD_NODES.forEach((_, node) => {
      const capacity = this.cloudCapacity[node] ?? 0;
      if ((this.cloudCount[node] ?? 0) < capacity && Math.random() < dt * 25) this.addCloud(node, false);
    });
    for (const c of this.cloud) {
      const centre = this.nodePoint(c.node);
      const size = this.cloudSize[c.node] ?? 0;
      c.reach = Math.min(1, c.reach + dt * 0.9);
      c.angle += c.spin * dt;
      const breathe = 1 + 0.08 * Math.sin(c.phase + time * 0.7);
      // On a phone the critics stand close together, so their clouds stay smaller.
      const scale = this.layout.compact ? 0.62 : 1;
      const radius =
        (CLOUD_INNER + CLOUD_REACH * size * c.depth) * scale * breathe * (0.35 + 0.65 * easeOut(c.reach));
      const x = centre.x + Math.cos(c.angle) * radius;
      const y = centre.y + Math.sin(c.angle) * radius * 0.86;
      if (c.fresh) {
        c.size = 0;
        c.fresh = false;
      }
      record(c, x, y, 1);
      c.nx = x;
      c.ny = y;
    }
  }

  private draw(): void {
    const { ctx, dpr } = this;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.lineCap = "round";

    // Sort every tail segment into one list per stream and brightness step, so each
    // list is a single stroke.
    for (const list of this.segments) list.count = 0;
    for (const p of this.particles) {
      if (p.stream === DORMANT || p.size < 2) continue;
      const visible = 0.16 + 0.64 * (this.current[p.stream] ?? 0);
      this.collect(p, visible, p.drain, this.segments, p.stream);
    }
    for (let s = 0; s < STREAMS.length; s++) {
      const intensity = this.current[s] ?? 0;
      ctx.strokeStyle = this.colours[s] ?? "#fff";
      // Faint streams stay hairlines; the working stream gets a line heavy enough
      // to survive a projector or a compressed screen recording, and no heavier:
      // past this the streaks merge into a painted ribbon.
      ctx.lineWidth = 0.75 + 0.55 * intensity * intensity;
      this.strokeLevels(this.segments, s);
    }

    for (const list of this.cloudSegments) list.count = 0;
    for (const c of this.cloud) if (c.size >= 2) this.collect(c, 0.85, 0, this.cloudSegments, c.node);
    ctx.lineWidth = 1.15;
    for (let node = 0; node < CLOUD_NODES.length; node++) {
      ctx.strokeStyle = this.cloudColours[node] ?? "#fff";
      this.strokeLevels(this.cloudSegments, node);
    }
    ctx.globalAlpha = 1;
  }

  /** Add a tail's segments, newest first, to the lists for `group` (a stream or a
   * node) by brightness. `age` shifts the whole tail older, as a retired streak fades. */
  private collect(t: Trail, visible: number, age: number, lists: Segments[], group: number): void {
    const { trail } = t;
    for (let j = 0; j < t.size - 1 && j + age < TRAIL_POINTS; j++) {
      const a = ((t.head - j + TRAIL_POINTS) % TRAIL_POINTS) * 3;
      const b = ((t.head - j - 1 + TRAIL_POINTS) % TRAIL_POINTS) * 3;
      const x = trail[a] ?? 0;
      const y = trail[a + 1] ?? 0;
      const level = Math.min(LEVELS, Math.round(visible * (trail[a + 2] ?? 0) * (AGE[j + age] ?? 0) * LEVELS));
      if (level <= 0 || this.inQuietZone(x, y)) continue;
      const list = lists[group * LEVELS + level - 1];
      if (list) pushSegment(list, trail[b] ?? 0, trail[b + 1] ?? 0, x, y);
    }
  }

  /** Stroke `group`'s lists, one per brightness step, in the current colour and width. */
  private strokeLevels(lists: Segments[], group: number): void {
    const { ctx } = this;
    for (let level = 1; level <= LEVELS; level++) {
      const list = lists[group * LEVELS + level - 1];
      if (!list?.count) continue;
      const { data, count } = list;
      ctx.beginPath();
      for (let i = 0; i < count * 4; i += 4) {
        ctx.moveTo(data[i] ?? 0, data[i + 1] ?? 0);
        ctx.lineTo(data[i + 2] ?? 0, data[i + 3] ?? 0);
      }
      ctx.globalAlpha = level / LEVELS;
      ctx.stroke();
    }
  }
}

/** Line segments waiting to be stroked: x0, y0, x1, y1 each, `count` of them. */
interface Segments {
  data: Float32Array;
  count: number;
}

function newSegments(): Segments {
  return { data: new Float32Array(1024), count: 0 };
}

function pushSegment(list: Segments, x0: number, y0: number, x1: number, y1: number): void {
  if ((list.count + 1) * 4 > list.data.length) {
    const grown = new Float32Array(list.data.length * 2);
    grown.set(list.data);
    list.data = grown;
  }
  const i = list.count * 4;
  list.data[i] = x0;
  list.data[i + 1] = y0;
  list.data[i + 2] = x1;
  list.data[i + 3] = y1;
  list.count++;
}

function newTrail(): Trail {
  return { trail: new Float32Array(TRAIL_POINTS * 3), head: 0, size: 0 };
}

function record(t: Trail, x: number, y: number, glow: number): void {
  t.head = (t.head + 1) % TRAIL_POINTS;
  const i = t.head * 3;
  t.trail[i] = x;
  t.trail[i + 1] = y;
  t.trail[i + 2] = glow;
  t.size = Math.min(t.size + 1, TRAIL_POINTS);
}

/** Move a whole tail, as when its node is dragged. */
function shiftTrail(t: Trail & { nx: number; ny: number }, dx: number, dy: number): void {
  for (let j = 0; j < t.size; j++) {
    const i = ((t.head - j + TRAIL_POINTS) % TRAIL_POINTS) * 3;
    t.trail[i] = (t.trail[i] ?? 0) + dx;
    t.trail[i + 1] = (t.trail[i + 1] ?? 0) + dy;
  }
  t.nx += dx;
  t.ny += dy;
}

function easeOut(t: number): number {
  return 1 - Math.pow(1 - t, 3);
}

function gaussian(): number {
  // Box-Muller, clamped to the stream's edges.
  const u = 1 - Math.random();
  const v = Math.random();
  const g = Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
  return Math.max(-1, Math.min(1, g * 0.55));
}
