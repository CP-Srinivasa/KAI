// Wärmekarte — Quellen-/Risiko-/Signalqualität (Konzept §6/§16/§17). CSS-Grid aus
// getönten Zellen, Intensität = Opazität. Pure Intensitäts-Skalierung getrennt.
import { cn } from "@/lib/utils";
import type { Tone } from "@/lib/tone";

export type HeatCell = { key: string; value: number | null; tone: Tone; title?: string };

/** Skaliert einen Wert auf eine Zell-Opazität 0.12..1 relativ zu max. Pure/testbar.
 *  null/≤0 → Minimal-Opazität (sichtbar, aber leer); max≤0 → alle minimal. */
export function heatOpacity(value: number | null, max: number): number {
  if (value == null || value <= 0 || max <= 0) return 0.12;
  return 0.12 + 0.88 * Math.min(1, value / max);
}

const TONE_BG: Record<Tone, string> = {
  pos: "bg-pos",
  neg: "bg-neg",
  warn: "bg-warn",
  info: "bg-info",
  ai: "bg-ai",
  neutral: "bg-fg-muted",
};

export function Heatmap({
  cells,
  columns = 6,
  className,
}: {
  cells: HeatCell[];
  columns?: number;
  className?: string;
}) {
  const max = cells.reduce((m, c) => Math.max(m, c.value ?? 0), 0);
  return (
    <div
      className={cn("grid gap-1", className)}
      style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}
    >
      {cells.map((c) => (
        <div
          key={c.key}
          title={c.title ?? `${c.key}: ${c.value ?? "—"}`}
          className={cn("aspect-square rounded-xs", TONE_BG[c.tone])}
          style={{ opacity: heatOpacity(c.value, max) }}
        />
      ))}
    </div>
  );
}
