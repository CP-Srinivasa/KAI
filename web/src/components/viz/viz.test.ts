import { describe, expect, it } from "vitest";
import { gaugeFraction } from "./Gauge";
import { donutSegments, type DonutDatum } from "./Donut";
import { funnelRows } from "./Funnel";
import { heatOpacity } from "./Heatmap";
import { railSegments } from "./TimelineRail";

describe("gaugeFraction", () => {
  it("clamps to [0,1] und skaliert linear in [min,max]", () => {
    expect(gaugeFraction(0.5)).toBe(0.5);
    expect(gaugeFraction(50, 0, 100)).toBe(0.5);
    expect(gaugeFraction(-10, 0, 100)).toBe(0); // unter min
    expect(gaugeFraction(200, 0, 100)).toBe(1); // über max
  });
  it("ist robust gegen kaputte Eingaben", () => {
    expect(gaugeFraction(NaN)).toBe(0);
    expect(gaugeFraction(5, 10, 10)).toBe(0); // max<=min
  });
});

describe("donutSegments", () => {
  const data: DonutDatum[] = [
    { label: "A", value: 3, tone: "info" },
    { label: "B", value: 1, tone: "pos" },
  ];
  it("berechnet Anteile, die sich zu 1 summieren", () => {
    const segs = donutSegments(data);
    expect(segs).toHaveLength(2);
    expect(segs[0].fraction).toBeCloseTo(0.75);
    expect(segs[1].fraction).toBeCloseTo(0.25);
    expect(segs.reduce((s, x) => s + x.fraction, 0)).toBeCloseTo(1);
  });
  it("erstes Segment startet bei Offset 0, zweites versetzt", () => {
    const segs = donutSegments(data);
    expect(segs[0].dashOffset).toBe(0);
    expect(segs[1].dashOffset).toBeLessThan(0);
  });
  it("filtert nicht-positive Werte und gibt [] bei Gesamtsumme 0", () => {
    expect(donutSegments([{ label: "z", value: 0, tone: "info" }])).toEqual([]);
    expect(donutSegments([])).toEqual([]);
    expect(donutSegments([
      { label: "A", value: 2, tone: "info" },
      { label: "neg", value: -5, tone: "neg" },
    ])).toHaveLength(1);
  });
});

describe("funnelRows", () => {
  it("Breite relativ zur ersten Stufe + Retention je Stufe", () => {
    const rows = funnelRows([
      { label: "empfangen", value: 100 },
      { label: "akzeptiert", value: 40 },
      { label: "gefüllt", value: 10 },
    ]);
    expect(rows[0].widthPct).toBe(100);
    expect(rows[0].retention).toBe(1);
    expect(rows[1].widthPct).toBe(40);
    expect(rows[1].retention).toBeCloseTo(0.4);
    expect(rows[2].retention).toBeCloseTo(0.25); // 10/40
  });
  it("Top=0 → alle Breiten 0; leer → []", () => {
    expect(funnelRows([{ label: "x", value: 0 }])[0].widthPct).toBe(0);
    expect(funnelRows([])).toEqual([]);
  });
});

describe("heatOpacity", () => {
  it("skaliert 0.12..1 relativ zu max", () => {
    expect(heatOpacity(10, 10)).toBeCloseTo(1);
    expect(heatOpacity(0, 10)).toBe(0.12);
    expect(heatOpacity(null, 10)).toBe(0.12);
    expect(heatOpacity(5, 10)).toBeCloseTo(0.56);
  });
  it("max<=0 → Minimal-Opazität", () => {
    expect(heatOpacity(5, 0)).toBe(0.12);
  });
});

describe("railSegments", () => {
  it("gleich breit ohne Gewichte", () => {
    const segs = railSegments([
      { key: "a", tone: "pos" },
      { key: "b", tone: "warn" },
      { key: "c", tone: "neg" },
    ]);
    for (const s of segs) expect(s.widthPct).toBeCloseTo(33.333, 3);
    expect(segs.reduce((t, s) => t + s.widthPct, 0)).toBeCloseTo(100);
  });
  it("proportional zu Gewichten", () => {
    const segs = railSegments([
      { key: "a", tone: "pos", weight: 3 },
      { key: "b", tone: "warn", weight: 1 },
    ]);
    expect(segs[0].widthPct).toBe(75);
    expect(segs[1].widthPct).toBe(25);
  });
});
