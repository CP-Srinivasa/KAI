import { describe, expect, it } from "vitest";
import {
  ROADMAPS,
  phaseStatusKind,
  phaseStatusTone,
  type PhaseStatus,
} from "./roadmaps";

describe("phaseStatus-Mapper", () => {
  const kinds: Array<[PhaseStatus, string]> = [
    ["done", "completed"],
    ["active", "active"],
    ["gated", "blocked"],
    ["planned", "pending"],
  ];
  it.each(kinds)("phaseStatusKind(%s) → %s", (s, expected) => {
    expect(phaseStatusKind(s)).toBe(expected);
  });

  const tones: Array<[PhaseStatus, string]> = [
    ["done", "pos"],
    ["active", "info"],
    ["gated", "warn"],
    ["planned", "neutral"],
  ];
  it.each(tones)("phaseStatusTone(%s) → %s", (s, expected) => {
    expect(phaseStatusTone(s)).toBe(expected);
  });
});

describe("ROADMAPS Datenintegrität", () => {
  it("nicht leer; jede Roadmap hat Titel + Phasen", () => {
    expect(ROADMAPS.length).toBeGreaterThan(0);
    for (const rm of ROADMAPS) {
      expect(rm.title.length).toBeGreaterThan(0);
      expect(rm.phases.length).toBeGreaterThan(0);
    }
  });
  it("Phasen-IDs je Roadmap eindeutig; Status gültig", () => {
    const valid: PhaseStatus[] = ["done", "active", "planned", "gated"];
    for (const rm of ROADMAPS) {
      const ids = rm.phases.map((p) => p.id);
      expect(new Set(ids).size).toBe(ids.length);
      for (const p of rm.phases) expect(valid).toContain(p.status);
    }
  });
});
