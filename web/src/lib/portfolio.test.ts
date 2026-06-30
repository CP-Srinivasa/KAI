import { describe, it, expect } from "vitest";
import { computeEquityComposition } from "./portfolio";

describe("computeEquityComposition", () => {
  it("returns all-zero for an empty book", () => {
    expect(computeEquityComposition([])).toEqual({
      longMarketValue: 0,
      shortLiability: 0,
      netPositionValue: 0,
    });
  });

  it("treats shorts as a liability and nets long − short", () => {
    // Mirrors a live snapshot: longs 6426.67, shorts 15464.49 → net −9037.82,
    // which reconciles to (total_equity − cash) on the backend.
    const c = computeEquityComposition([
      { position_side: "long", market_value_usd: 4035.62 },
      { position_side: "short", market_value_usd: 9785.30 },
      { position_side: "short", market_value_usd: 1523.97 },
      { position_side: "long", market_value_usd: 2391.05 },
      { position_side: "short", market_value_usd: 4155.22 },
    ]);
    expect(c.longMarketValue).toBeCloseTo(6426.67, 2);
    expect(c.shortLiability).toBeCloseTo(15464.49, 2);
    expect(c.netPositionValue).toBeCloseTo(-9037.82, 2);
  });

  it("defaults a missing side to long", () => {
    const c = computeEquityComposition([{ market_value_usd: 100 }]);
    expect(c.longMarketValue).toBe(100);
    expect(c.shortLiability).toBe(0);
    expect(c.netPositionValue).toBe(100);
  });

  it("counts an unpriced position (null market value) as 0, like the backend", () => {
    const c = computeEquityComposition([
      { position_side: "short", market_value_usd: null },
      { position_side: "long", market_value_usd: 50 },
    ]);
    expect(c.shortLiability).toBe(0);
    expect(c.longMarketValue).toBe(50);
    expect(c.netPositionValue).toBe(50);
  });
});
