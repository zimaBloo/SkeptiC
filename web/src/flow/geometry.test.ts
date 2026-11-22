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

  it("keeps a dragged node clear of the edges, the top line and the status line", () => {
    expect(clampToStage(1440, 900, { x: -50, y: 10 })).toEqual({ x: 24, y: 96 });
    expect(clampToStage(1440, 900, { x: 5000, y: 5000 })).toEqual({ x: 1416, y: 790 });
    expect(layout(1440, 900, { idea: { fx: 0, fy: 1 } }).idea).toEqual({ x: 24, y: 790 });
  });

  it("routes the streams to a node's new place", () => {
    const moved = layout(1440, 900, { risk: { fx: 0.3, fy: 0.8 } });
    expect(curve(moved, "in:risk").p3).toEqual(moved.critics.risk);
    expect(curve(moved, "out:risk").p0).toEqual(moved.critics.risk);
  });

  it("gives a critic dragged past the verdict room up to the screen's edge", () => {
    const moved = layout(1440, 900, { market: { fx: 0.9, fy: 0.2 } });
    expect(labelRoom(moved, "market")).toBe(1440 - 1296 - 40);
    const plain = layout(1440, 900);
    expect(labelRoom(plain, "market")).toBe(plain.combiner.x - plain.critics.market.x - 48);
  });

  it("does not let a critic's label run over a verdict just ahead of it at the same height", () => {
    const close = layout(1440, 900, { technical: { fx: 0.56, fy: 0.52 }, combiner: { fx: 0.625, fy: 0.52 } });
    expect(labelRoom(close, "technical")).toBe(60);
  });
});
