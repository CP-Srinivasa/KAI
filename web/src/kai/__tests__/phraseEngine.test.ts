// Vitest unit tests for KAI phrase engine.
// Spec: docs/kai_persona/technical_ui_pack_v3_2.md §17.1 + §17.3 (safety)

import { describe, it, expect } from "vitest";
import { getKaiPhrase, getKaiExtraModePhrase, isPhraseSafe } from "../phraseEngine";
import { KAI_FORBIDDEN_PHRASES_DE, KAI_FORBIDDEN_PHRASES_EN } from "../constants";
import type { KaiState } from "../types";

const STATES: KaiState[] = ["IDLE", "ANALYSIS", "SIGNAL", "WARNING", "SECURITY", "ERROR", "OFFLINE"];

describe("getKaiPhrase", () => {
  it("returns a non-empty DE phrase for every state", () => {
    for (const s of STATES) {
      const p = getKaiPhrase(s, "de", 0);
      expect(p.length).toBeGreaterThan(0);
    }
  });

  it("returns a non-empty EN phrase for every state", () => {
    for (const s of STATES) {
      const p = getKaiPhrase(s, "en", 0);
      expect(p.length).toBeGreaterThan(0);
    }
  });

  it("seed produces deterministic output for tests", () => {
    const a = getKaiPhrase("ANALYSIS", "de", 1);
    const b = getKaiPhrase("ANALYSIS", "de", 1);
    expect(a).toBe(b);
  });

  it("does not produce any forbidden DE financial-claim phrases anywhere", () => {
    for (const s of STATES) {
      for (let seed = 0; seed < 50; seed++) {
        const p = getKaiPhrase(s, "de", seed);
        for (const banned of KAI_FORBIDDEN_PHRASES_DE) {
          expect(p.toLowerCase()).not.toContain(banned.toLowerCase());
        }
      }
    }
  });

  it("does not produce any forbidden EN financial-claim phrases", () => {
    for (const s of STATES) {
      for (let seed = 0; seed < 50; seed++) {
        const p = getKaiPhrase(s, "en", seed);
        for (const banned of KAI_FORBIDDEN_PHRASES_EN) {
          expect(p.toLowerCase()).not.toContain(banned.toLowerCase());
        }
      }
    }
  });
});

describe("getKaiExtraModePhrase", () => {
  it("returns a phrase for hype, mockery, bad_data", () => {
    expect(getKaiExtraModePhrase("hype", "de", 0).length).toBeGreaterThan(0);
    expect(getKaiExtraModePhrase("mockery", "de", 0).length).toBeGreaterThan(0);
    expect(getKaiExtraModePhrase("bad_data", "de", 0).length).toBeGreaterThan(0);
  });
});

describe("isPhraseSafe", () => {
  it("rejects guaranteed-profit claims (DE)", () => {
    expect(isPhraseSafe("Das ist ein sicherer Gewinn", "de")).toBe(false);
    expect(isPhraseSafe("Garantierter Gewinn auf BTC", "de")).toBe(false);
    expect(isPhraseSafe("100% sicher", "de")).toBe(false);
  });

  it("rejects guaranteed-profit claims (EN)", () => {
    expect(isPhraseSafe("This is a guaranteed profit", "en")).toBe(false);
    expect(isPhraseSafe("Risk-free profit on ETH", "en")).toBe(false);
  });

  it("accepts neutral observation phrases", () => {
    expect(isPhraseSafe("Datenstrom stabil. Ich sehe ein Muster.", "de")).toBe(true);
    expect(isPhraseSafe("Signal alive. Risk still needs a leash.", "en")).toBe(true);
  });
});
