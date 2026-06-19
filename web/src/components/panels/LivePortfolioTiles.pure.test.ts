import { describe, it, expect } from "vitest";
import { computeAllocation } from "./LivePortfolioTiles";
import type { PaperPosition } from "@/lib/api";

// Money formatting moved to the canonical SSOT — covered by lib/money.test.ts.

function pos(
  symbol: string,
  market_value_usd: number | null,
  display_value_usd: number | null = null,
): PaperPosition {
  return {
    symbol,
    quantity: 1,
    avg_entry_price: 1,
    stop_loss: null,
    take_profit: null,
    market_price: 1,
    market_value_usd,
    display_value_usd,
    unrealized_pnl_usd: null,
  } as PaperPosition;
}

describe("computeAllocation", () => {
  it("empty positions -> empty/total 0", () => {
    expect(computeAllocation([])).toEqual({ items: [], total: 0 });
  });
  it("marks priced positions, sorts by value desc", () => {
    const { items, total } = computeAllocation([
      pos("BTC/USDT", 300),
      pos("ETH/USDT", 700),
      pos("Z/USDT", null), // no price, no display value -> dropped
      pos("Y/USDT", 0),
    ]);
    expect(total).toBe(1000);
    expect(items.map((i) => i.symbol)).toEqual(["ETH/USDT", "BTC/USDT"]);
    expect(items.every((i) => i.marked)).toBe(true);
    expect(items[0].pct).toBeCloseTo(70);
    expect(items[1].pct).toBeCloseTo(30);
    expect(items.reduce((s, i) => s + i.pct, 0)).toBeCloseTo(100);
  });
  it("falls back to entry-basis display_value_usd for unpriced microcaps (marked=false)", () => {
    // ZEREBRO-Fall: kein Live-Kurs, aber Einstandswert vorhanden -> sichtbar, nicht versteckt.
    const { items, total } = computeAllocation([
      pos("BTC/USDT", 600),
      pos("ZEREBRO/USDT", null, 4388.93),
    ]);
    expect(total).toBeCloseTo(4988.93);
    const z = items.find((i) => i.symbol === "ZEREBRO/USDT");
    expect(z).toBeDefined();
    expect(z!.marked).toBe(false);
    expect(z!.value).toBeCloseTo(4388.93);
    expect(items.find((i) => i.symbol === "BTC/USDT")!.marked).toBe(true);
  });
  it("uses absolute value for shorts (negative market value)", () => {
    const { total } = computeAllocation([pos("S/USDT", -400), pos("L/USDT", 600)]);
    expect(total).toBe(1000);
  });
});
