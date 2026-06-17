// Pure Helfer für das "Akute Punkte"-Board (WP-1.3 / Konzept §7). Filtert die
// Truth-Chips auf die handlungsrelevanten (kritisch/warn) und liefert je Punkt
// eine knappe empfohlene Aktion. Getrennt von der Komponente → testbar.
//
// EHRLICH: deckt nur die real ableitbaren Kategorien ab (blockierende Gates +
// akute Probleme aus den Truth-Chips). Todos/Phasen/Verbesserungen aus Konzept
// §7 haben KEINE strukturierte Datenquelle und werden NICHT gefaket — sie
// brauchen erst ein Operator-Board-Backend.
import type { TruthChip, TruthTone } from "@/lib/truthStatus";

export const ATTENTION_TONES: ReadonlySet<TruthTone> = new Set<TruthTone>(["critical", "warn"]);

/** Akute (handlungsrelevante) Chips — bereits nach Dringlichkeit sortiert (deriveTruthChips). */
export function acuteChips(chips: TruthChip[]): TruthChip[] {
  return chips.filter((c) => ATTENTION_TONES.has(c.tone));
}

const ACTION_BY_KEY: Record<string, string> = {
  "entry-mode": "Entry-Mode + offene Routen im Schutzschalter prüfen.",
  priority: "Trading-Loop-Heartbeat + High-P-Beleg verifizieren.",
  source: "Quellenbasis prüfen — 0 trusted ist nicht institutionell belastbar.",
  paper: "Lifetime-Zahlen nicht als aktuellen 24h-Fortschritt lesen.",
  reentry: "Neue Gate-Definition setzen oder als Historie akzeptieren.",
  signal: "Low-P-Baseline / Tier-Lift belegen, bevor High-P als Qualität gilt.",
  "shadow-attribution": "Generator-Feed/Flag prüfen — nur 'real' zählt fürs Edge-Gate.",
};

/** Knappe empfohlene Aktion zu einem Chip-Key (Fallback: generisch). */
export function recommendedAction(key: string): string {
  return ACTION_BY_KEY[key] ?? "Status prüfen und Ursache klären.";
}
