// Vitest tests for risk guards.
// Spec: docs/kai_persona/technical_ui_pack_v3_2.md §17.3

import { describe, it, expect } from "vitest";
import { validateSignalForLivetrade, validateSignalInvariants } from "../riskGuards";
import type { KaiSignalCardData } from "../types";

function baseSignal(overrides: Partial<KaiSignalCardData> = {}): KaiSignalCardData {
  return {
    asset: "BTC/USDT",
    mode: "LIVETRADE",
    direction: "LONG",
    confidence: 70,
    risk: "MEDIUM",
    entry: "78000",
    stopLoss: "76500",
    dataBasis: ["news", "volume", "structure"],
    dataQuality: "MEDIUM",
    timestamp: "2026-05-03T00:00:00Z",
    comment: "ok",
    ...overrides,
  };
}

describe("validateSignalForLivetrade", () => {
  it("allows a clean LIVETRADE", () => {
    const r = validateSignalForLivetrade(baseSignal());
    expect(r.allowed).toBe(true);
  });

  it("blocks CRITICAL risk", () => {
    const r = validateSignalForLivetrade(baseSignal({ risk: "CRITICAL" }));
    expect(r.allowed).toBe(false);
    expect(r.reasons.join(" ")).toContain("Critical Risk");
  });

  it("blocks LOW data quality", () => {
    const r = validateSignalForLivetrade(baseSignal({ dataQuality: "LOW" }));
    expect(r.allowed).toBe(false);
    expect(r.reasons.join(" ")).toContain("Datenqualitaet");
  });

  it("blocks UNKNOWN data quality", () => {
    const r = validateSignalForLivetrade(baseSignal({ dataQuality: "UNKNOWN" }));
    expect(r.allowed).toBe(false);
  });

  it("blocks missing stop-loss", () => {
    const r = validateSignalForLivetrade(baseSignal({ stopLoss: "" }));
    expect(r.allowed).toBe(false);
    expect(r.reasons.join(" ")).toContain("Stop-Loss");
  });

  it("blocks waiting/not-confirmed stop-loss text", () => {
    expect(validateSignalForLivetrade(baseSignal({ stopLoss: "wartet auf Struktur" })).allowed).toBe(false);
    expect(validateSignalForLivetrade(baseSignal({ stopLoss: "not confirmed yet" })).allowed).toBe(false);
    expect(validateSignalForLivetrade(baseSignal({ stopLoss: "still waiting" })).allowed).toBe(false);
  });

  it("blocks empty data basis", () => {
    const r = validateSignalForLivetrade(baseSignal({ dataBasis: [] }));
    expect(r.allowed).toBe(false);
    expect(r.reasons.join(" ")).toContain("Datenbasis");
  });

  it("blocks confidence outside 0-100", () => {
    expect(validateSignalForLivetrade(baseSignal({ confidence: -1 })).allowed).toBe(false);
    expect(validateSignalForLivetrade(baseSignal({ confidence: 101 })).allowed).toBe(false);
    expect(validateSignalForLivetrade(baseSignal({ confidence: NaN })).allowed).toBe(false);
  });

  it("non-LIVETRADE modes pass without validation", () => {
    expect(validateSignalForLivetrade(baseSignal({ mode: "WATCHLIST", risk: "CRITICAL" })).allowed).toBe(true);
    expect(validateSignalForLivetrade(baseSignal({ mode: "PAPERTRADE", dataQuality: "LOW" })).allowed).toBe(true);
  });

  it("collects ALL violations, not just first", () => {
    const r = validateSignalForLivetrade(
      baseSignal({ risk: "CRITICAL", dataQuality: "LOW", stopLoss: "wartet" }),
    );
    expect(r.allowed).toBe(false);
    expect(r.reasons.length).toBeGreaterThanOrEqual(3);
  });
});

describe("validateSignalInvariants", () => {
  it("rejects malformed asset", () => {
    expect(validateSignalInvariants(baseSignal({ asset: "BTC" })).allowed).toBe(false);
    expect(validateSignalInvariants(baseSignal({ asset: "" })).allowed).toBe(false);
  });

  it("rejects out-of-range confidence", () => {
    expect(validateSignalInvariants(baseSignal({ confidence: 150 })).allowed).toBe(false);
  });
});
