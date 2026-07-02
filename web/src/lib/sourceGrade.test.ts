import { describe, expect, it } from "vitest";
import { gradeOf, topFlop } from "./sourceGrade";
import type { SourceRankEntry } from "@/lib/api";

function e(
  source_name: string,
  point_estimate: number | null,
  opts: Partial<SourceRankEntry> = {},
): SourceRankEntry {
  return {
    source_name,
    rank: 0,
    lifecycle_tier: "ranked",
    reliability_tier: "neutral",
    provisional: true,
    wilson_lower_95: null,
    n: 20,
    hits: 0,
    point_estimate,
    silent: false,
    pinned: false,
    rotation_flagged: false,
    consecutive_top_runs: 0,
    logical_status: "active",
    last_signal_at: null,
    ...opts,
  };
}

describe("gradeOf", () => {
  it("klassifiziert nach Trefferquote-Schwellen", () => {
    expect(gradeOf(0.81)).toBe("pos");
    expect(gradeOf(0.55)).toBe("pos"); // Grenze inklusiv
    expect(gradeOf(0.5)).toBe("warn");
    expect(gradeOf(0.45)).toBe("warn"); // Grenze inklusiv
    expect(gradeOf(0.44)).toBe("neg");
    expect(gradeOf(0.0)).toBe("neg");
    expect(gradeOf(null)).toBe("muted");
  });
});

describe("topFlop", () => {
  it("Stärkste = höchste Trefferquote (nur mit Wert), max 3", () => {
    const { strong } = topFlop([
      e("a", 0.81),
      e("b", 0.61),
      e("c", 0.6),
      e("d", 0.37),
      e("z", null), // keine Quote → nicht in strong
    ]);
    expect(strong.map((s) => s.source_name)).toEqual(["a", "b", "c"]);
  });

  it("Schwächste: Rotation/verstummt zuerst, dann niedrigste Quote; dedupe gegen strong", () => {
    const { strong, weak } = topFlop([
      e("a", 0.81),
      e("b", 0.61),
      e("c", 0.6),
      e("decrypt", 0.37, { rotation_flagged: true }),
      e("theblock", 0.545, { silent: true, rotation_flagged: true }),
      e("tv", 0.429, { rotation_flagged: true }),
      e("clean", 0.4), // schwach aber nicht geflaggt
    ]);
    const strongNames = new Set(strong.map((s) => s.source_name));
    // keine Überschneidung
    expect(weak.every((w) => !strongNames.has(w.source_name))).toBe(true);
    // geflaggte/verstummte zuerst, untereinander nach Quote asc; clean fällt raus
    expect(weak.map((s) => s.source_name)).toEqual(["decrypt", "tv", "theblock"]);
  });

  it("leere Liste → strong/weak leer", () => {
    expect(topFlop([])).toEqual({ strong: [], weak: [] });
  });
});
