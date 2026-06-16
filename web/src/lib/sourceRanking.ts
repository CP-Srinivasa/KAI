// Pure Top-5/Flop-5-Rangliste der Quellen (WP-2.1 / Konzept §6). Basis:
// per_source_active_precision.by_source aus dem Quality-Report. Gewertet werden
// NUR Quellen mit ausreichender Stichprobe (n_threshold_met) und vorhandener
// Trefferquote — sonst ist ein Ranking nicht belastbar (kein Fake-Ranking auf
// 1-2 Resolves). Getrennt von der Seite → testbar.
import { sourceLabel } from "@/lib/sourceLabels";
import type { DashboardQuality } from "@/lib/api";

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
