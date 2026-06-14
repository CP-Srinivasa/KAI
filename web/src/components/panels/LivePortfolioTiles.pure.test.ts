import { describe, it, expect } from "vitest";
import { computeAllocation } from "./LivePortfolioTiles";
import type { PaperPosition } from "@/lib/api";

// Money formatting moved to the canonical SSOT — covered by lib/money.test.ts.

function pos(symbol: string, market_value_usd: number | null): PaperPosition {
  return {
    symbol,
    quantity: 1,
    avg_entry_price: 1,
    stop_loss: null,
    take_profit: null,
    market_price: 1,
    market_value_usd,
    unrealized_pnl_usd: null,
  } as PaperPosition;
}

describe("computeAllocation", () => {
  it("empty positions -> empty/total 0", () => {
    expect(computeAllocation([])).toEqual({ items: [], total: 0 });
  });
  it("skips unpriced positions and sorts by value desc", () => {
    const { items, total } = computeAllocation([
      pos("BTC/USDT", 300),
      pos("ETH/USDT", 700),
      pos("X/USDT", null),
      pos("Y/USDT", 0),
    ]);
    expect(total).toBe(1000);
    expect(items.map((i) => i.symbol)).toEqual(["ETH/USDT", "BTC/USDT"]);
    expect(items[0].pct).toBeCloseTo(70);
    expect(items[1].pct).toBeCloseTo(30);
    expect(items.reduce((s, i) => s + i.pct, 0)).toBeCloseTo(100);
  });
  it("uses absolute value for shorts (negative market value)", () => {
    const { total } = computeAllocation([pos("S/USDT", -400), pos("L/USDT", 600)]);
    expect(total).toBe(1000);
  });
});
