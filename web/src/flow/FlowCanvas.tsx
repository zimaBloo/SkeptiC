import { useEffect, useRef } from "react";
import { CRITICS } from "../api";
import { FlowField, type Intensities, type StreamColours } from "./field";
import type { StreamId } from "./geometry";

interface Props {
  intensities: Intensities;
  /** Draw a still picture instead of moving streaks. */
  still?: boolean;
  className?: string;
  count?: number;
}

/** The flow field on a canvas that fills its parent. */
export function FlowCanvas({ intensities, still = false, className, count }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const fieldRef = useRef<FlowField | null>(null);
  const intensitiesRef = useRef(intensities);
  intensitiesRef.current = intensities;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const field = new FlowField(canvas, { colours: streamColours(canvas), count });
    fieldRef.current = field;
    field.setIntensities(intensitiesRef.current, true);

    const size = () => {
      const rect = canvas.getBoundingClientRect();
      field.resize(rect.width, rect.height, Math.min(window.devicePixelRatio || 1, 2));
      if (still) field.drawStill();
    };
    // Keep the streaks clear of the labels laid over the canvas.
    const measureLabels = () => {
      const host = canvas.parentElement;
      if (!host) return;
      const origin = canvas.getBoundingClientRect();
      const pad = 6;
      field.setQuietZones(
        Array.from(host.querySelectorAll<HTMLElement>("[data-quiet]")).map((el) => {
          const r = el.getBoundingClientRect();
          return {
            x: r.left - origin.left - pad,
            y: r.top - origin.top - pad,
            width: r.width + pad * 2,
            height: r.height + pad * 2,
          };
        }),
      );
    };
    const labelTimer = window.setInterval(() => {
      measureLabels();
      if (still) field.drawStill();
    }, 500);
    measureLabels();
    size();
    const observer = new ResizeObserver(size);
    observer.observe(canvas);

    // A window dragged to a screen with another pixel density keeps its CSS size,
    // so the resize observer stays quiet; watch the density itself.
    let density: MediaQueryList | null = null;
    const onDensity = () => {
      size();
      watchDensity();
    };
    const watchDensity = () => {
      density?.removeEventListener("change", onDensity);
      density = window.matchMedia(`(resolution: ${window.devicePixelRatio || 1}dppx)`);
      density.addEventListener("change", onDensity);
    };
    watchDensity();

    const onVisibility = () => {
      if (still) return;
      if (document.hidden) field.stop();
      else field.start();
    };
    document.addEventListener("visibilitychange", onVisibility);

    const onMove = (event: PointerEvent) => {
      const rect = canvas.getBoundingClientRect();
      field.setPointer({ x: event.clientX - rect.left, y: event.clientY - rect.top });
    };
    const onLeave = () => field.setPointer(null);
    if (!still) {
      window.addEventListener("pointermove", onMove, { passive: true });
      document.documentElement.addEventListener("pointerleave", onLeave);
      field.start();
    }

    return () => {
      field.stop();
      window.clearInterval(labelTimer);
      observer.disconnect();
      density?.removeEventListener("change", onDensity);
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("pointermove", onMove);
      document.documentElement.removeEventListener("pointerleave", onLeave);
      fieldRef.current = null;
    };
  }, [still, count]);

  useEffect(() => {
    const field = fieldRef.current;
    if (!field) return;
    field.setIntensities(intensities);
    if (still) field.drawStill();
  }, [intensities, still]);

  return <canvas ref={canvasRef} className={className} aria-hidden="true" />;
}

/** Each stream's colour: the ink, tinted towards its critic's hue. */
function streamColours(element: Element): StreamColours {
  const style = getComputedStyle(element);
  const ink = style.getPropertyValue("--ink").trim() || "#ece8e1";
  const entries: [StreamId, string][] = [];
  for (const critic of CRITICS) {
    const hue = style.getPropertyValue(`--${critic}`).trim() || ink;
    entries.push([`in:${critic}`, mix(ink, hue, 0.55)]);
    entries.push([`out:${critic}`, mix(ink, hue, 0.4)]);
  }
  return Object.fromEntries(entries) as StreamColours;
}

function mix(a: string, b: string, amount: number): string {
  const pa = parseHex(a);
  const pb = parseHex(b);
  const channel = (i: number) => Math.round((pa[i] ?? 0) * (1 - amount) + (pb[i] ?? 0) * amount);
  return `rgb(${channel(0)}, ${channel(1)}, ${channel(2)})`;
}

function parseHex(hex: string): number[] {
  const value = hex.replace("#", "");
  return [0, 2, 4].map((i) => Number.parseInt(value.slice(i, i + 2), 16));
}
