import { describe, expect, it } from "vitest";
import { allocationDonutData, concentrationTone } from "./executiveSnapshot";
import type { DiversificationAssetRow } from "@/lib/api";

function row(base: string, weight_pct: number | null): DiversificationAssetRow {
  return {
    symbol: `${base}/USDT`,
    base,
    exposure_usd: null,
    weight_pct,
    exposure_basis: "",
    asset_horizon: "",
    position_horizon: "",
    sector: "",
    narrative: "",
    correlation_group: "",
    risk_tier: "",
    liquidity_tier: "",
    is_reserve: false,
    evaluable: true,
    source: "",
  };
}

describe("concentrationTone", () => {
  it("hoher Anteil = schlecht (neg ≥60, warn ≥40, sonst pos)", () => {
    expect(concentrationTone(70)).toBe("neg");
    expect(concentrationTone(60)).toBe("neg");
    expect(concentrationTone(45)).toBe("warn");
    expect(concentrationTone(20)).toBe("pos");
  });
  it("null/undefined → neutral (keine Daten)", () => {
    expect(concentrationTone(null)).toBe("neutral");
    expect(concentrationTone(undefined)).toBe("neutral");
  });
});

describe("allocationDonutData", () => {
  it("sortiert nach Gewicht, behält Top-N, bündelt Rest als 'Übrige'", () => {
    const rows = [
      row("BTC", 40),
      row("ETH", 25),
      row("SOL", 15),
      row("AVAX", 10),
      row("DOT", 6),
      row("ADA", 4),
    ];
    const out = allocationDonutData(rows, 3);
    expect(out.map((d) => d.label)).toEqual(["BTC", "ETH", "SOL", "Übrige"]);
    expect(out[3].value).toBe(20); // 10+6+4
    expect(out[3].tone).toBe("neutral");
  });

  it("filtert nicht-positive/fehlende Gewichte; ohne Rest kein 'Übrige'", () => {
    const out = allocationDonutData([row("BTC", 60), row("X", 0), row("Y", null), row("ETH", 40)]);
    expect(out.map((d) => d.label)).toEqual(["BTC", "ETH"]);
  });

  it("leer/keine Daten → []", () => {
    expect(allocationDonutData(undefined)).toEqual([]);
    expect(allocationDonutData([])).toEqual([]);
    expect(allocationDonutData([row("X", 0)])).toEqual([]);
  });
});
