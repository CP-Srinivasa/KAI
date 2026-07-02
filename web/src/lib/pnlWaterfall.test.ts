import { describe, expect, it } from "vitest";
import { waterfallBars } from "@/components/viz/Waterfall";
import { realizedToWaterfall } from "./pnlWaterfall";
import type { RealizedByAssetEntry } from "@/lib/api";

function entry(symbol: string, realized_pnl_usd: number): RealizedByAssetEntry {
  return {
    symbol,
    realized_pnl_usd,
    closed_trades: 1,
    wins: 1,
    losses: 0,
    win_rate_pct: 100,
    fees_usd_total: 0,
    partial_closes: 0,
    full_closes: 1,
    last_close_utc: null,
  };
}

describe("waterfallBars", () => {
  it("baut laufende Summe + Gesamt-Balken + Domain", () => {
    const { bars, min, max } = waterfallBars([
      { label: "A", value: 100 },
      { label: "B", value: -40 },
    ]);
    expect(bars).toHaveLength(3); // A, B, Gesamt
    expect(bars[0]).toMatchObject({ start: 0, end: 100, value: 100 });
    expect(bars[1]).toMatchObject({ start: 100, end: 60, value: -40 });
    expect(bars[2]).toMatchObject({ label: "Gesamt", start: 0, end: 60, isTotal: true });
    expect(min).toBe(0);
    expect(max).toBe(100);
  });
  it("ohne Total optional", () => {
    const { bars } = waterfallBars([{ label: "A", value: 10 }], false);
    expect(bars).toHaveLength(1);
  });
});

describe("realizedToWaterfall", () => {
  it("Top-N nach Betrag, Rest als 'Übrige', Anzeige nach Wert", () => {
    const rows = [
      entry("A", 100),
      entry("B", -80),
      entry("C", 30),
      entry("D", -5),
      entry("E", 2),
    ];
    const out = realizedToWaterfall(rows, 3);
    // Top-3 nach |Wert|: A(100), B(-80), C(30); D+E gebündelt (-3)
    expect(out.map((d) => d.label)).toEqual(["A", "C", "B", "Übrige"]);
    expect(out.find((d) => d.label === "Übrige")?.value).toBe(-3);
  });
  it("filtert 0-Werte; leer → []", () => {
    expect(realizedToWaterfall([entry("Z", 0)])).toEqual([]);
    expect(realizedToWaterfall(undefined)).toEqual([]);
  });
});
