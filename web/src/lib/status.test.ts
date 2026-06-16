import { describe, expect, it } from "vitest";
import {
  STATUS_REGISTRY,
  compareStatus,
  getStatus,
  mostUrgent,
  statusSeverity,
  statusTone,
  type StatusKind,
  type StatusTone,
} from "./status";

const ALL_KINDS: StatusKind[] = [
  "operational",
  "idle",
  "active",
  "degraded",
  "blocked",
  "fail-closed",
  "execution-off",
  "write-back-locked",
  "dry-run",
  "paper",
  "real",
  "shadow",
  "live",
  "verified",
  "unverified",
  "stale",
  "duplicate",
  "rejected",
  "pending",
  "completed",
  "urgent",
  "critical",
];

const VALID_TONES: StatusTone[] = ["neutral", "pos", "neg", "warn", "info", "ai", "muted"];

describe("STATUS_REGISTRY — Vollständigkeit (SSOT)", () => {
  it("deckt alle 22 kanonischen Zustände aus Konzept §22 ab", () => {
    expect(Object.keys(STATUS_REGISTRY).sort()).toEqual([...ALL_KINDS].sort());
  });

  it("jeder Deskriptor ist vollständig: Label, gültiger Ton, Icon, Tooltip", () => {
    for (const kind of ALL_KINDS) {
      const d = getStatus(kind);
      expect(d.kind).toBe(kind);
      expect(d.label.length).toBeGreaterThan(0);
      expect(VALID_TONES).toContain(d.tone);
      // lucide-Icons sind React-Komponenten (forwardRef-Objekt ODER Funktion).
      expect(["object", "function"]).toContain(typeof d.icon);
      expect(d.icon).toBeTruthy();
      expect(d.tooltip.length).toBeGreaterThan(0);
      expect(typeof d.action).toBe("string"); // "" erlaubt (keine Aktion nötig)
    }
  });

  it("severity ist dicht und eindeutig (0..n-1)", () => {
    const sevs = ALL_KINDS.map((k) => statusSeverity(k)).sort((a, b) => a - b);
    expect(sevs).toEqual(Array.from({ length: ALL_KINDS.length }, (_, i) => i));
  });
});

describe("Semantik-Anker", () => {
  it("kritisch ist der dringlichste Zustand", () => {
    expect(statusSeverity("critical")).toBe(0);
    for (const k of ALL_KINDS) {
      if (k !== "critical") expect(statusSeverity(k)).toBeGreaterThan(0);
    }
  });

  it("Echtgeld-/Live-/Kritisch-Zustände sind rot (neg)", () => {
    expect(statusTone("real")).toBe("neg");
    expect(statusTone("live")).toBe("neg");
    expect(statusTone("critical")).toBe("neg");
  });

  it("gesunde/bestätigte Zustände sind grün (pos)", () => {
    expect(statusTone("operational")).toBe("pos");
    expect(statusTone("verified")).toBe("pos");
    expect(statusTone("completed")).toBe("pos");
  });

  it("bewusst-aus/neutral ist muted, nicht alarmierend", () => {
    expect(statusTone("execution-off")).toBe("muted");
    expect(statusTone("idle")).toBe("muted");
    expect(statusTone("shadow")).toBe("muted");
  });
});

describe("Helfer", () => {
  it("compareStatus sortiert kritisch vor gesund", () => {
    const sorted = [...ALL_KINDS].sort(compareStatus);
    expect(sorted[0]).toBe("critical");
    expect(sorted[sorted.length - 1]).toBe("completed");
  });

  it("mostUrgent liefert den dringlichsten Zustand", () => {
    expect(mostUrgent(["operational", "stale", "critical", "idle"])).toBe("critical");
    expect(mostUrgent(["operational", "idle"])).toBe("idle");
    expect(mostUrgent([])).toBeNull();
  });
});
