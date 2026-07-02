// V-DB5 audit A-1/A-2/A-3 + D-1 (2026-05-09):
// Single-source-of-truth fuer Tier-Lift-Rendering. Vorher gab es zwei
// Inline-IIFE-Helfer (Dashboard.tsx KpiCard-Tile + QualityBar-Row), die
// leicht divergieren konnten (".toFixed(1)pp" vs ".toFixed(2)pp"). Plus
// das akademische "n.s."-Marker durch deutschen "Lift unsicher" ersetzt.

import type { DashboardQuality } from "@/lib/api";
import { tierLiftTone, type Tone } from "@/lib/tone";

export { tierLiftTone };
export type { Tone };

/**
 * Kanonisches Format fuer Tier-Lift (Differenz in Prozentpunkten).
 * Vorzeichenstabil, eine Nachkommastelle: "+3.2pp" / "-1.5pp" / "0.0pp" / "—".
 */
export function formatTierLift(pp: number | null | undefined): string {
  if (pp == null) return "—";
  const sign = pp >= 0 ? "+" : "";
  return `${sign}${pp.toFixed(1)}pp`;
}

export type TierLiftSignificance = {
  /** Gesamt-Sample n = high_conviction_resolved + standard_resolved. null wenn Felder fehlen. */
  sampleN: number | null;
  /** true = CIs getrennt, false = Overlap (Lift unsicher), null = CIs unbekannt. */
  isSignificant: boolean | null;
};

/**
 * Berechnet n + Signifikanz-Status aus den Tier-Feldern einer DashboardQuality.
 * Wilson-95%-CIs ueberlappen → Lift-Differenz statistisch nicht trennbar.
 */
export function evaluateTierLiftSignificance(
  data: DashboardQuality | null,
): TierLiftSignificance {
  const hcResolved = data?.priority_tier_high_conviction_resolved;
  const stdResolved = data?.priority_tier_standard_resolved;
  if (hcResolved == null || stdResolved == null) {
    return { sampleN: null, isSignificant: null };
  }
  const sampleN = hcResolved + stdResolved;
  const hLo = data?.priority_tier_high_conviction_ci_low_pct;
  const hHi = data?.priority_tier_high_conviction_ci_high_pct;
  const sLo = data?.priority_tier_standard_ci_low_pct;
  const sHi = data?.priority_tier_standard_ci_high_pct;
  if (hLo == null || hHi == null || sLo == null || sHi == null) {
    return { sampleN, isSignificant: null };
  }
  const ciOverlap = hLo <= sHi && sLo <= hHi;
  return { sampleN, isSignificant: !ciOverlap };
}

/** D-1: Klartext-Marker fuer "Lift unsicher" (statt akademisches "n.s."). */
export const TIER_LIFT_INSIGNIFICANT_LABEL = "Lift unsicher";

/** Tooltip-Text — single-source fuer Tile + QualityBar-Row. */
export const TIER_LIFT_INSIGNIFICANT_TOOLTIP =
  "Wilson 95% Konfidenzintervalle der beiden Hit-Rates überlappen → Lift-Differenz statistisch nicht trennbar (n zu klein).";
