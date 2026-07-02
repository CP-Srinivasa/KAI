import { describe, expect, it } from "vitest";
import { topFlopSources, rankProvenanceSources } from "./sourceRanking";
import type { ProvenanceMetrics } from "@/lib/api";

type Metrics = Parameters<typeof topFlopSources>[0] extends infer T
  ? T extends Record<string, infer M> | undefined
    ? M
    : never
  : never;

function m(hit_rate_pct: number | null, n_threshold_met: boolean, passes_gate = false): Metrics {
  return {
    resolved: 30,
    hits: 0,
    misses: 0,
    hit_rate_pct,
    ci_low_pct: null,
    ci_high_pct: null,
    n_threshold_met,
    wilson_low_threshold_met: false,
    passes_gate,
  } as Metrics;
}

describe("topFlopSources", () => {
  it("wertet nur Quellen mit Stichprobe + Trefferquote, sortiert top desc / flop asc", () => {
    const by = {
      a: m(80, true),
      b: m(20, true),
      c: m(55, true),
      d: m(90, true),
      e: m(40, true),
      f: m(10, true),
      g: m(99, false), // zu wenig Stichprobe → ignoriert
      h: m(null, true), // keine Quote → ignoriert
    };
    const { top, flop } = topFlopSources(by, 2);
    expect(top.map((s) => s.name)).toEqual(["d", "a"]); // 90, 80
    expect(flop.map((s) => s.name)).toEqual(["f", "b"]); // 10, 20 (schlechteste zuerst)
  });

  it("≤N wertbare Quellen → alles top, flop leer (keine Doppelung)", () => {
    const { top, flop } = topFlopSources({ a: m(70, true), b: m(50, true) }, 5);
    expect(top.map((s) => s.name)).toEqual(["a", "b"]);
    expect(flop).toEqual([]);
  });

  it("undefined / nur unwertbare → leer", () => {
    expect(topFlopSources(undefined)).toEqual({ top: [], flop: [] });
    expect(topFlopSources({ x: m(50, false), y: m(null, true) })).toEqual({ top: [], flop: [] });
  });
});

function pm(source: string, hit_rate_pct: number | null, sample_sufficient: boolean): ProvenanceMetrics {
  return {
    source,
    resolved: 12,
    hits: 0,
    misses: 0,
    hit_rate_pct,
    ci_low_pct: null,
    ci_high_pct: null,
    ci_width_pct: null,
    sample_sufficient,
    inconclusive: 0,
  };
}

describe("rankProvenanceSources", () => {
  it("sortiert nach Trefferquote desc, filtert unknown, behält dünne (markiert)", () => {
    const ranked = rankProvenanceSources([
      pm("a", 40, true),
      pm("unknown", 99, true), // unknown-Bucket raus
      pm("b", 80, false), // dünn → bleibt, sufficient=false
      pm("c", 60, true),
    ]);
    expect(ranked.map((s) => s.name)).toEqual(["b", "c", "a"]); // 80, 60, 40
    expect(ranked.find((s) => s.name === "b")?.sufficient).toBe(false);
  });

  it("undefined → leer; null-Quote sortiert ans Ende", () => {
    expect(rankProvenanceSources(undefined)).toEqual([]);
    const ranked = rankProvenanceSources([pm("a", null, true), pm("b", 30, true)]);
    expect(ranked.map((s) => s.name)).toEqual(["b", "a"]);
  });
});
