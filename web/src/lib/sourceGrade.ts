// Güte-Einstufung + Top/Flop für die Quellen-Güte-Rangliste (Phase 0 / DALI-P-201).
// Pure, ohne React → testbar. Basis: SourceRankEntry aus /dashboard/api/source-lifecycle.
import type { SourceRankEntry } from "@/lib/api";

// Güte-Schwellen auf die echte Trefferquote (point_estimate). 50% = Münzwurf.
export const GOOD = 0.55;
export const MID = 0.45;

export type Grade = "pos" | "warn" | "neg" | "muted";

/** Güte-Klasse aus der Trefferquote: ≥0.55 gut, ≥0.45 mittel, <0.45 schwach,
 *  null → unbewertbar. Steuert Ampel-Farbe und Headline-Tönung. */
export function gradeOf(p: number | null): Grade {
  if (p == null) return "muted";
  if (p >= GOOD) return "pos";
  if (p >= MID) return "warn";
  return "neg";
}

/** Top/Flop aus derselben Liste — keine zweite SSOT, kein Gate-Filter.
 *  Stärkste = höchste echte Trefferquote (nur mit Wert). Schwächste =
 *  Rotation/verstummt zuerst, sonst niedrigste Quote; dedupliziert gegen
 *  die Stärksten (eine Quelle erscheint nie in beiden Streifen). */
export function topFlop(entries: SourceRankEntry[]): {
  strong: SourceRankEntry[];
  weak: SourceRankEntry[];
} {
  const strong = entries
    .filter((e) => e.point_estimate != null)
    .slice()
    .sort((a, b) => (b.point_estimate as number) - (a.point_estimate as number))
    .slice(0, 3);
  const strongNames = new Set(strong.map((e) => e.source_name));
  const weak = entries
    .filter((e) => !strongNames.has(e.source_name))
    .slice()
    .sort((a, b) => {
      const af = a.rotation_flagged || a.silent ? 1 : 0;
      const bf = b.rotation_flagged || b.silent ? 1 : 0;
      if (af !== bf) return bf - af; // geflaggte/verstummte zuerst
      const ap = a.point_estimate ?? 2; // null → ans Ende der asc-Sortierung
      const bp = b.point_estimate ?? 2;
      return ap - bp;
    })
    .slice(0, 3);
  return { strong, weak };
}
