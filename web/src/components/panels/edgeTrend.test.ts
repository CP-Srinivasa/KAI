import { describe, expect, it } from "vitest";
import { metricSeries } from "./EdgeTrendCard";
import type { EdgeWindow } from "@/lib/api";

function w(precision_pct: number | null, brier: number | null, ic_1h: number | null): EdgeWindow {
  return { window_start: "", window_end: "", resolved: 0, precision_pct, brier, ic_1h };
}

describe("metricSeries", () => {
  it("nimmt nur non-null-Fenster und merkt sich den letzten Wert", () => {
    const windows = [w(60, 0.2, 0.1), w(null, null, null), w(55, 0.25, 0.05)];
    const p = metricSeries(windows, "precision_pct");
    expect(p.points.map((d) => d.y)).toEqual([60, 55]);
    expect(p.points.map((d) => d.x)).toEqual([0, 2]); // null-Fenster (idx 1) ausgelassen, x bleibt Original-Index
    expect(p.latest).toBe(55);
  });

  it("alle null → leere Serie, latest null", () => {
    const out = metricSeries([w(null, null, null), w(null, null, null)], "brier");
    expect(out.points).toEqual([]);
    expect(out.latest).toBeNull();
  });

  it("greift die richtige Metrik", () => {
    const windows = [w(60, 0.2, 0.1), w(50, 0.3, -0.2)];
    expect(metricSeries(windows, "ic_1h").points.map((d) => d.y)).toEqual([0.1, -0.2]);
    expect(metricSeries(windows, "ic_1h").latest).toBe(-0.2);
  });
});
