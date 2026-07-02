// Pure Top-5/Flop-5-Rangliste der Quellen (WP-2.1 / Konzept §6). Basis:
// per_source_active_precision.by_source aus dem Quality-Report. Gewertet werden
// NUR Quellen mit ausreichender Stichprobe (n_threshold_met) und vorhandener
// Trefferquote — sonst ist ein Ranking nicht belastbar (kein Fake-Ranking auf
// 1-2 Resolves). Getrennt von der Seite → testbar.
import { sourceLabel } from "@/lib/sourceLabels";
import type { DashboardQuality, ProvenanceMetrics } from "@/lib/api";

type BySource = NonNullable<DashboardQuality["per_source_active_precision"]>["by_source"];

export type SourceRank = {
  name: string;
  label: string;
  hitRate: number;
  resolved: number;
  passesGate: boolean;
};

/** Liefert Top-N (beste Trefferquote) + Flop-N (schlechteste). Bei ≤N wertbaren
 *  Quellen: alles als `top`, `flop` leer (keine künstliche Doppelung). Pure/testbar. */
export function topFlopSources(
  bySource: BySource | undefined,
  n = 5,
): { top: SourceRank[]; flop: SourceRank[] } {
  if (!bySource) return { top: [], flop: [] };
  const ranked: SourceRank[] = Object.entries(bySource)
    .filter(([, m]) => m.hit_rate_pct != null && m.n_threshold_met)
    .map(([name, m]) => ({
      name,
      label: sourceLabel(name).label,
      hitRate: m.hit_rate_pct as number,
      resolved: m.resolved,
      passesGate: m.passes_gate,
    }))
    .sort((a, b) => b.hitRate - a.hitRate);

  if (ranked.length <= n) return { top: ranked, flop: [] };
  const top = ranked.slice(0, n);
  const flop = ranked.slice(-n).sort((a, b) => a.hitRate - b.hitRate);
  return { top, flop };
}

// Zweite, eigenständige Trefferquoten-Sicht: Provenance (by_source) ist eine
// ANDERE SSOT als per_source_active_precision — sie misst den getaggten
// Signalfluss (resolved/hits) ohne den unknown-Bucket. Als Ranking-Bars auf der
// Quellen-Seite zeigt sie, ob beide Sichten konvergieren. Kein Gate-Filter hier:
// schwache Stichproben werden NICHT versteckt, sondern als `sufficient=false`
// markiert (die Bar rendert dann gedämpft, kein Fake-Vertrauen). Pure/testbar.
export type ProvenanceRank = {
  name: string;
  label: string;
  hitRate: number | null;
  resolved: number;
  sufficient: boolean;
};

export function rankProvenanceSources(
  bySource: ProvenanceMetrics[] | undefined,
): ProvenanceRank[] {
  if (!bySource) return [];
  return bySource
    .filter((m) => m.source && m.source !== "unknown")
    .map((m) => ({
      name: m.source,
      label: sourceLabel(m.source).label,
      hitRate: m.hit_rate_pct,
      resolved: m.resolved,
      sufficient: m.sample_sufficient,
    }))
    .sort((a, b) => (b.hitRate ?? -1) - (a.hitRate ?? -1));
}
