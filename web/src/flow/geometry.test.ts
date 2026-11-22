import { describe, expect, it } from "vitest";
import { CRITICS } from "../api";
import { CAPSULE_HEIGHT, clampToStage, curve, labelRoom, layout } from "./geometry";

describe("layout with dragged nodes", () => {
  it("puts a dragged node at its fraction of the stage and leaves the others in place", () => {
    const plain = layout(1440, 900);
    const moved = layout(1440, 900, { market: { fx: 0.5, fy: 0.3 } });
    expect(moved.critics.market).toEqual({ x: 720, y: 270 });
    expect(moved.critics.technical).toEqual(plain.critics.technical);
    expect(moved.combiner).toEqual(plain.combiner);
  });

  it("keeps the arrangement's shape when the window is resized", () => {
    const placement = { risk: { fx: 0.7, fy: 0.4 } };
    expect(layout(1000, 800, placement).critics.risk).toEqual({ x: 700, y: 320 });
    expect(layout(2000, 1000, placement).critics.risk).toEqual({ x: 1400, y: 400 });
  });

  it("keeps a dragged critic clear of the stage's edges, the inlet and the outlet", () => {
    expect(clampToStage(1440, 900, { x: -50, y: 10 })).toEqual({ x: 28, y: 140 });
    expect(clampToStage(1440, 900, { x: 5000, y: 5000 })).toEqual({ x: 1412, y: 718 });
    const l = layout(1440, 900, { market: { fx: 0, fy: 0 }, technical: { fx: 1, fy: 1 } });
    expect(l.critics.market.y).toBeGreaterThan(l.inlet.y + CAPSULE_HEIGHT / 2);
    expect(l.critics.technical.y).toBeLessThan(l.outlet.y - CAPSULE_HEIGHT / 2);
  });

  it("keeps the idea and the verdict where the flow enters and leaves, whatever is saved", () => {
    const plain = layout(1440, 900);
    const saved = layout(1440, 900, { idea: { fx: 0, fy: 1 }, combiner: { fx: 0.1, fy: 0.1 } });
    expect(saved.idea).toEqual(plain.idea);
    expect(saved.combiner).toEqual(plain.combiner);
    expect(plain.idea).toEqual({ x: 720, y: 28 + CAPSULE_HEIGHT / 2 });
  });

  it("pours each critic's streams out of the inlet's lower edge and into the outlet's upper edge", () => {
    const l = layout(1440, 900);
    const starts = CRITICS.map((c) => curve(l, `in:${c}`).p0);
    const ends = CRITICS.map((c) => curve(l, `out:${c}`).p3);
    for (const p of starts) expect(p.y).toBe(l.inlet.y + CAPSULE_HEIGHT / 2);
    for (const p of ends) expect(p.y).toBe(l.outlet.y - CAPSULE_HEIGHT / 2);
    // Spread along the capsules in the critics' order, within the capsules' width.
    const xs = starts.map((p) => p.x);
    expect([...xs].sort((a, b) => a - b)).toEqual(xs);
    expect(Math.min(...xs)).toBeGreaterThan(l.inlet.x - l.inlet.width / 2);
    expect(Math.max(...xs)).toBeLessThan(l.inlet.x + l.inlet.width / 2);
  });

  it("fits the capsules inside a phone's width", () => {
    const l = layout(375, 760);
    expect(l.compact).toBe(true);
    expect(l.inlet.width).toBeLessThanOrEqual(375 - 32);
    expect(l.outlet.width).toBe(l.inlet.width);
  });

  it("runs the panel top to bottom: the idea above the critics' row, the verdict below", () => {
    const l = layout(1440, 900);
    const ys = Object.values(l.critics).map((p) => p.y);
    expect(new Set(ys).size).toBe(1);
    expect(l.idea.y).toBeLessThan(ys[0] as number);
    expect(l.combiner.y).toBeGreaterThan(ys[0] as number);
    expect(l.idea.x).toBe(720);
  });

  it("routes the streams to a node's new place", () => {
    const moved = layout(1440, 900, { risk: { fx: 0.3, fy: 0.8 } });
    expect(curve(moved, "in:risk").p3).toEqual(moved.critics.risk);
    expect(curve(moved, "out:risk").p0).toEqual(moved.critics.risk);
  });

  it("ends a critic's label before the next critic in the row, the last one at the edge", () => {
    const l = layout(1440, 900);
    expect(labelRoom(l, "market")).toBe(l.critics.technical.x - l.critics.market.x - 44);
    expect(labelRoom(l, "risk")).toBe(1440 - 12 - l.critics.risk.x - 44);
  });

  it("gives a critic dragged out of the row room up to the screen's edge", () => {
    const moved = layout(1440, 900, { market: { fx: 0.3, fy: 0.3 } });
    expect(labelRoom(moved, "market")).toBe(1440 - 12 - 432 - 44);
  });
});
