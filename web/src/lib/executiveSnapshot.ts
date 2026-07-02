// Pure Helfer für den Executive Snapshot (WP-1.2). Getrennt von der Komponente,
// damit testbar (executiveSnapshot.test.ts).
import type { Tone } from "@/lib/tone";
import type { DonutDatum } from "@/components/viz/Donut";
import type { DiversificationAssetRow } from "@/lib/api";

/** Klumpenrisiko-Ton: hoher Anteil = schlecht. null → neutral (keine Daten). */
export function concentrationTone(pct: number | null | undefined): Tone {
  if (pct == null) return "neutral";
  if (pct >= 60) return "neg";
  if (pct >= 40) return "warn";
  return "pos";
}

// Kategoriale Palette für Allocation-Slices (NICHT semantisch — nur Unterscheidung).
const SLICE_TONES: Tone[] = ["info", "ai", "pos", "warn", "neutral"];

/** Asset-Verteilung → Donut-Daten: Top-N nach Gewicht, Rest gebündelt als
 *  "Übrige". Nicht-positive/fehlende Gewichte werden ignoriert. Pure/testbar. */
export function allocationDonutData(
  rows: DiversificationAssetRow[] | undefined,
  maxSlices = 5,
): DonutDatum[] {
  if (!rows || rows.length === 0) return [];
  const priced = rows
    .map((r) => ({ label: r.base || r.symbol, value: r.weight_pct ?? 0 }))
    .filter((d) => Number.isFinite(d.value) && d.value > 0)
    .sort((a, b) => b.value - a.value);
  if (priced.length === 0) return [];
  const top = priced.slice(0, maxSlices);
  const restSum = priced.slice(maxSlices).reduce((s, d) => s + d.value, 0);
  const data: DonutDatum[] = top.map((d, i) => ({
    label: d.label,
    value: d.value,
    tone: SLICE_TONES[i % SLICE_TONES.length],
  }));
  if (restSum > 0) data.push({ label: "Übrige", value: restSum, tone: "neutral" });
  return data;
}
