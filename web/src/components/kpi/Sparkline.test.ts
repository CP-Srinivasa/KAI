import { describe, expect, it } from "vitest";
import { sparkPolyline } from "./Sparkline";

describe("sparkPolyline", () => {
  it("returns null for <2 points (nothing meaningful to draw)", () => {
    expect(sparkPolyline([])).toBeNull();
    expect(sparkPolyline([{ x: 0, y: 1 }])).toBeNull();
  });

  it("spans the full viewBox width from first to last point", () => {
    const pts = sparkPolyline([
      { x: 0, y: 0 },
      { x: 1, y: 10 },
      { x: 2, y: 5 },
    ]);
    expect(pts).not.toBeNull();
    const coords = pts!.split(" ").map((p) => p.split(",").map(Number));
    expect(coords[0][0]).toBe(0); // first x at left edge
    expect(coords[coords.length - 1][0]).toBe(92); // last x at right edge
    expect(coords).toHaveLength(3);
  });

  it("places a flat series at vertical mid (no NaN from zero span)", () => {
    const pts = sparkPolyline([
      { x: 0, y: 5 },
      { x: 1, y: 5 },
    ]);
    expect(pts).not.toBeNull();
    for (const p of pts!.split(" ")) {
      const [px, py] = p.split(",").map(Number);
      expect(Number.isFinite(px)).toBe(true);
      expect(Number.isFinite(py)).toBe(true);
    }
  });

  it("draws higher y closer to the top (smaller py)", () => {
    const pts = sparkPolyline([
      { x: 0, y: 0 },
      { x: 1, y: 100 },
    ])!;
    const [, low] = pts.split(" ")[0].split(",").map(Number);
    const [, high] = pts.split(" ")[1].split(",").map(Number);
    expect(high).toBeLessThan(low); // y=100 renders above y=0
  });
});
