import { describe, expect, it } from "vitest";
import { clampToStage, curve, labelRoom, layout } from "./geometry";

describe("layout with dragged nodes", () => {
  it("puts a dragged node at its fraction of the stage and leaves the others in place", () => {
    const plain = layout(1440, 900);
    const moved = layout(1440, 900, { market: { fx: 0.5, fy: 0.3 } });
    expect(moved.critics.market).toEqual({ x: 720, y: 270 });
    expect(moved.critics.technical).toEqual(plain.critics.technical);
    expect(moved.combiner).toEqual(plain.combiner);
  });

  it("keeps the arrangement's shape when the window is resized", () => {
    const placement = { combiner: { fx: 0.7, fy: 0.4 } };
    expect(layout(1000, 800, placement).combiner).toEqual({ x: 700, y: 320 });
    expect(layout(2000, 1000, placement).combiner).toEqual({ x: 1400, y: 400 });
  });

  it("keeps a dragged node clear of the stage's edges and the status line", () => {
    expect(clampToStage(1440, 900, { x: -50, y: 10 })).toEqual({ x: 28, y: 40 });
    expect(clampToStage(1440, 900, { x: 5000, y: 5000 })).toEqual({ x: 1412, y: 828 });
    expect(layout(1440, 900, { idea: { fx: 0, fy: 1 } }).idea).toEqual({ x: 28, y: 828 });
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
