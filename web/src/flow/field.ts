// The flow field: thin streaks drawn along each stream's curve with fading
// trails, in the manner of the Wind Map and earth.nullschool. A stream's
// intensity (0 to 1) sets how many streaks it carries and how fast they move.
// The pointer pushes nearby streaks aside; they drift back to their path.

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
/** How big each node's cloud is, from 0 (none) to 1 (the largest). */
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

interface Particle {
  stream: number; // index into STREAMS, or DORMANT while no stream has work
  t: number;
  speed: number; // px per second along the curve at full intensity
  offset: number; // -1..1 across the stream
  wobble: number;
  phase: number;
  dx: number; // displacement from the pointer
  dy: number;
  x: number; // where the streak was drawn to last frame
  y: number;
  nx: number; // where it is now
  ny: number;
  fresh: boolean;
}

/** A streak that has arrived at a finished step and now circles its node. */
interface CloudParticle {
  node: number; // index into CLOUD_NODES
  angle: number;
  /** Where in the cloud it settles, 0 (inner edge) to 1 (outer edge). */
  depth: number;
  /** How far out it has drifted so far, 0 to 1: arrivals start at the node and spread. */
  reach: number;
  spin: number; // radians per second
  phase: number;
  x: number;
  y: number;
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
const TRAIL_FADE = 0.085; // share of the trail removed per 60 Hz frame
const INTENSITY_EASE = 1.6; // per second
const IDLE_FLOOR = 0.035; // calm streams still drift
const BANDS = 4; // brightness steps along a streak's life
const DORMANT = -1; // a streak waiting for a stream with work
// Waiting streaks join streams with work at a steady rate: the whole pool over about
// one trip along a stream, so a starting stream fills evenly instead of in a slug.
const JOIN_SECONDS = 3.5;
// The trail fade multiplies alpha, and on an 8-bit canvas small values round back to
// themselves (3/255 stays 3/255), leaving a grey ghost of every streak. Every
// TRIM_EVERY frames the canvas is redrawn through a filter that subtracts TRIM_STEP
// from alpha, which clears that residue and barely shortens a live trail.
const TRIM_EVERY = 15;
const TRIM_STEP = 4 / 255;
const TRIM_FILTER_ID = "skeptic-trail-trim";
// A full cloud holds this many streaks; a smaller one fewer, by its size.
const CLOUD_MAX = 170;
// Radius of a cloud's inner edge and how far a full cloud reaches past it, in px.
const CLOUD_INNER = 22;
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
  private readonly buckets: Particle[][] = Array.from({ length: STREAMS.length * BANDS }, () => []);
  private frame = 0;
  private frames = 0;
  private joinBudget = 0;
  private last = 0;
  private running = false;
  private readonly canTrim: boolean;
  private readonly cloud: CloudParticle[] = [];
  private cloudSize = new Float32Array(CLOUD_NODES.length);
  private cloudCount = new Int32Array(CLOUD_NODES.length);
  private readonly cloudColours: string[];

  constructor(
    private readonly canvas: HTMLCanvasElement,
    options: FieldOptions,
  ) {
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("canvas 2d context unavailable");
    this.ctx = ctx;
    this.canTrim = typeof ctx.filter === "string" && ensureTrimFilter();
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
    if (jumped.size) for (const p of this.particles) if (jumped.has(p.stream)) p.fresh = true;
  }

  /** Set each finished step's cloud size. Streaks that reach a finished node join its
   * cloud until it is full; with `immediate` (a finished run opened later) the clouds
   * are filled at once. A size of 0 empties that node's cloud. */
  setClouds(clouds: Clouds, immediate = false): void {
    CLOUD_NODES.forEach((node, i) => {
      const size = Math.min(1, Math.max(0, clouds[node] ?? 0));
      this.cloudSize[i] = size;
      const capacity = Math.round(size * CLOUD_MAX);
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
      x: 0,
      y: 0,
      nx: 0,
      ny: 0,
      fresh: true,
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
        this.draw(dt);
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
    // Clouds as still rings of dots, their size the same as in motion.
    CLOUD_NODES.forEach((_, node) => {
      const size = this.cloudSize[node] ?? 0;
      if (size === 0) return;
      const centre = this.nodePoint(node);
      ctx.fillStyle = this.cloudColours[node] ?? "#fff";
      ctx.globalAlpha = 0.5;
      const dots = Math.round(size * 60);
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
    return {
      stream,
      t: scatter ? Math.random() : Math.random() * 0.04,
      speed: 150 + Math.random() * 130,
      offset: gaussian() * 0.5,
      wobble: (Math.random() - 0.5) * 0.35,
      phase: Math.random() * Math.PI * 2,
      dx: 0,
      dy: 0,
      x: 0,
      y: 0,
      nx: 0,
      ny: 0,
      fresh: true,
    };
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
        p = this.spawn(false);
        this.particles[k] = p;
        if (p.stream === DORMANT) continue;
      }
      const length = this.lengths[p.stream] ?? 1;
      p.t += (p.speed * dt * (stopping ? 1 : 0.25 + 0.75 * intensity)) / length;
      if (p.t >= 1) {
        // A streak that reaches a finished step stays there, in its cloud.
        const node = this.endNode(p.stream);
        if (stopping && node >= 0 && (this.cloudCount[node] ?? 0) < Math.round((this.cloudSize[node] ?? 0) * CLOUD_MAX)) {
          this.addCloud(node, false);
        }
        p = this.spawn(false);
        this.particles[k] = p;
        if (p.stream === DORMANT) continue;
      }
      const c = this.curves[p.stream];
      if (!c) continue;
      const base = pointAt(c, p.t);
      const n = normalAt(c, p.t);
      const across =
        (p.offset + p.wobble * Math.sin(p.phase + p.t * 9)) * bundleWidth(p.t) * this.layout.spread;
      const bx = base.x + n.x * across;
      const by = base.y + n.y * across;
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
        p.x = x;
        p.y = y;
        p.fresh = false;
      }
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
      const capacity = Math.round((this.cloudSize[node] ?? 0) * CLOUD_MAX);
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
        c.x = x;
        c.y = y;
        c.fresh = false;
      }
      c.nx = x;
      c.ny = y;
    }
  }

  private drawClouds(): void {
    const { ctx } = this;
    ctx.lineWidth = 0.9;
    CLOUD_NODES.forEach((_, node) => {
      ctx.beginPath();
      let any = false;
      for (const c of this.cloud) {
        if (c.node !== node) continue;
        if (!this.inQuietZone(c.nx, c.ny)) {
          ctx.moveTo(c.x, c.y);
          ctx.lineTo(c.nx, c.ny);
          any = true;
        }
        c.x = c.nx;
        c.y = c.ny;
      }
      if (!any) return;
      ctx.strokeStyle = this.cloudColours[node] ?? "#fff";
      ctx.globalAlpha = 0.55;
      ctx.stroke();
    });
    ctx.globalAlpha = 1;
  }

  private draw(dt: number): void {
    const { ctx, dpr } = this;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    // Fade the previous frame towards transparent instead of clearing it.
    ctx.globalCompositeOperation = "destination-out";
    ctx.fillStyle = `rgba(0,0,0,${1 - Math.pow(1 - TRAIL_FADE, dt * 60)})`;
    ctx.fillRect(0, 0, this.canvas.width, this.canvas.height);
    // (Drawing a canvas of zero width or height throws, so a collapsed one is skipped.)
    if (this.canTrim && ++this.frames % TRIM_EVERY === 0 && this.canvas.width > 0 && this.canvas.height > 0) {
      // Redraw the canvas onto itself through the alpha-subtracting filter.
      ctx.globalCompositeOperation = "copy";
      ctx.filter = `url(#${TRIM_FILTER_ID})`;
      ctx.drawImage(this.canvas, 0, 0);
      ctx.filter = "none";
    }
    ctx.globalCompositeOperation = "source-over";
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.lineCap = "round";

    // Sort the streaks into one bucket per stream and brightness band (fainter near
    // the nodes), so each bucket is a single stroke.
    const buckets = this.buckets;
    for (const bucket of buckets) bucket.length = 0;
    for (const p of this.particles) {
      if (p.stream === DORMANT) continue;
      const edge = Math.min(1, p.t / 0.07, (1 - p.t) / 0.07);
      const band = Math.min(BANDS, Math.max(1, Math.ceil(edge * BANDS)));
      buckets[p.stream * BANDS + band - 1]?.push(p);
    }
    for (let s = 0; s < STREAMS.length; s++) {
      const intensity = this.current[s] ?? 0;
      const visible = 0.16 + 0.64 * intensity;
      ctx.strokeStyle = this.colours[s] ?? "#fff";
      // Faint streams stay hairlines; the working stream gets a line heavy enough
      // to survive a projector or a compressed screen recording, and no heavier:
      // past this the streaks merge into a painted ribbon.
      ctx.lineWidth = 0.75 + 0.55 * intensity * intensity;
      for (let band = 1; band <= BANDS; band++) {
        const bucket = buckets[s * BANDS + band - 1];
        if (!bucket?.length) continue;
        ctx.beginPath();
        let any = false;
        for (const p of bucket) {
          if (!this.inQuietZone(p.nx, p.ny)) {
            ctx.moveTo(p.x, p.y);
            ctx.lineTo(p.nx, p.ny);
            any = true;
          }
          p.x = p.nx;
          p.y = p.ny;
        }
        if (!any) continue;
        ctx.globalAlpha = visible * (band / BANDS);
        ctx.stroke();
      }
    }
    ctx.globalAlpha = 1;
    this.drawClouds();
  }
}

function easeOut(t: number): number {
  return 1 - Math.pow(1 - t, 3);
}

/** Put the trail-trim filter in the document once: alpha minus TRIM_STEP, colour
 * untouched. Returns false where the document cannot hold it. */
function ensureTrimFilter(): boolean {
  if (typeof document === "undefined") return false;
  if (document.getElementById(TRIM_FILTER_ID)) return true;
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("width", "0");
  svg.setAttribute("height", "0");
  svg.style.position = "absolute";
  const filter = document.createElementNS(ns, "filter");
  filter.id = TRIM_FILTER_ID;
  filter.setAttribute("color-interpolation-filters", "sRGB");
  filter.setAttribute("x", "0");
  filter.setAttribute("y", "0");
  filter.setAttribute("width", "1");
  filter.setAttribute("height", "1");
  const transfer = document.createElementNS(ns, "feComponentTransfer");
  const alpha = document.createElementNS(ns, "feFuncA");
  alpha.setAttribute("type", "linear");
  alpha.setAttribute("slope", "1");
  alpha.setAttribute("intercept", String(-TRIM_STEP));
  transfer.append(alpha);
  filter.append(transfer);
  svg.append(filter);
  document.body.append(svg);
  return true;
}

function gaussian(): number {
  // Box-Muller, clamped to the stream's edges.
  const u = 1 - Math.random();
  const v = Math.random();
  const g = Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
  return Math.max(-1, Math.min(1, g * 0.55));
}
