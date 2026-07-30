// Operator-Befund 2026-07-30: die Roadmaps-Seite zeigte „Snapshot · 2026-07-04"
// — 26 Tage alt, als muted Badge ohne jede Frische-Bewertung. Ein veralteter
// Snapshot sah damit genauso aus wie ein frischer.
//
// Gleiches Prinzip wie beim Operator-Board (#613): eine CHRONIK abgeschlossener
// Phasen kann nicht veralten. Von 15 Roadmap-Phasen sind 12 `done` — nur die
// OFFENEN (active/gated/planned) können ungepflegt sein, und nur auf die darf
// sich der Frische-Hinweis beziehen.
import { describe, expect, it } from "vitest";

import { ROADMAPS, roadmapFreshness } from "./roadmaps";

const DONE_ONLY = [
  { id: "a", label: "A", status: "done" as const },
  { id: "b", label: "B", status: "done" as const },
];
const WITH_OPEN = [
  { id: "a", label: "A", status: "done" as const },
  { id: "b", label: "B", status: "active" as const },
  { id: "c", label: "C", status: "gated" as const },
];

describe("roadmapFreshness", () => {
  it("reine Chronik veraltet NIE — auch nach Monaten", () => {
    const f = roadmapFreshness(DONE_ONLY, "2026-01-01", new Date("2026-07-30"));

    expect(f.openCount).toBe(0);
    expect(f.isStale).toBe(false);
    expect(f.tone).toBe("muted");
  });

  it("offene Phasen jenseits der Schwelle sind ungepflegt", () => {
    const f = roadmapFreshness(WITH_OPEN, "2026-07-04", new Date("2026-07-30"));

    expect(f.openCount).toBe(2);
    expect(f.ageDays).toBe(26);
    expect(f.isStale).toBe(true);
    expect(f.tone).toBe("warn");
  });

  it("frisch geprüft → kein Hinweis, obwohl Phasen offen sind", () => {
    const f = roadmapFreshness(WITH_OPEN, "2026-07-30", new Date("2026-07-30"));

    expect(f.openCount).toBe(2);
    expect(f.ageDays).toBe(0);
    expect(f.isStale).toBe(false);
  });

  it("genau auf der Schwelle noch nicht stale, einen Tag darüber schon", () => {
    expect(roadmapFreshness(WITH_OPEN, "2026-07-23", new Date("2026-07-30")).isStale).toBe(false);
    expect(roadmapFreshness(WITH_OPEN, "2026-07-22", new Date("2026-07-30")).isStale).toBe(true);
  });

  it("unparsebares Datum erfindet keine Alterung", () => {
    const f = roadmapFreshness(WITH_OPEN, "kaputt", new Date("2026-07-30"));

    expect(f.ageDays).toBeNull();
    expect(f.isStale).toBe(false);
  });

  it("Label benennt die Lage konkret statt nur ein Datum zu zeigen", () => {
    const chronicle = roadmapFreshness(DONE_ONLY, "2026-01-01", new Date("2026-07-30"));
    expect(chronicle.label).toMatch(/abgeschlossen/);
    expect(chronicle.label).not.toMatch(/ungepflegt/);

    const stale = roadmapFreshness(WITH_OPEN, "2026-07-04", new Date("2026-07-30"));
    expect(stale.label).toMatch(/2 offen/);
    expect(stale.label).toMatch(/26 Tage/);
  });
});

describe("Roadmap-Phasen mit maschinenlesbarer Quelle", () => {
  it("T4 ist an seine versiegelte Prä-Reg gebunden, nicht an einen Fliesstext", () => {
    // Ohne diese Bindung war der T4-Status eine eingefrorene Behauptung im
    // Quellcode; jetzt kommt er aus prereg_ledger minus prereg_verdicts.
    const all = ROADMAPS.flatMap((r) => r.phases);
    const t4 = all.find((p) => p.id === "t4");

    expect(t4?.prereg).toBe("c489079289070a8c");
  });

  it("jede prereg-Bindung ist eine 16-hex-id (deterministischer Prä-Reg-Key)", () => {
    const bound = ROADMAPS.flatMap((r) => r.phases).filter((p) => p.prereg);

    expect(bound.length).toBeGreaterThan(0);
    for (const p of bound) {
      expect(p.prereg).toMatch(/^[0-9a-f]{16}$/);
    }
  });

  it("nur OFFENE Phasen dürfen gebunden sein — erledigte brauchen keine Live-Quelle", () => {
    const bound = ROADMAPS.flatMap((r) => r.phases).filter((p) => p.prereg);

    for (const p of bound) {
      expect(p.status).not.toBe("done");
    }
  });
});
