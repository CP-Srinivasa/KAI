// Pure Helfer: realized-PnL-by-asset → Wasserfall-Beiträge (WP-3.4 / Konzept §14).
// Top-N nach Betrags-Beitrag, Rest gebündelt als "Übrige", Anzeige-Reihenfolge
// nach Wert (Gewinner → Verlierer) für eine lesbare Rise-then-Fall-Kurve.
import type { WaterfallInput } from "@/components/viz/Waterfall";
import type { RealizedByAssetEntry } from "@/lib/api";

export function realizedToWaterfall(
  rows: RealizedByAssetEntry[] | undefined,
  n = 8,
): WaterfallInput[] {
  if (!rows || rows.length === 0) return [];
  const items = rows
    .map((r) => ({ label: r.symbol, value: r.realized_pnl_usd }))
    .filter((d) => Number.isFinite(d.value) && d.value !== 0);
  if (items.length === 0) return [];
  const byAbs = [...items].sort((a, b) => Math.abs(b.value) - Math.abs(a.value));
  const top = byAbs.slice(0, n).sort((a, b) => b.value - a.value);
  const restSum = byAbs.slice(n).reduce((s, d) => s + d.value, 0);
  if (restSum !== 0) top.push({ label: "Übrige", value: restSum });
  return top;
}
