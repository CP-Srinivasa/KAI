// Wasserfall-Diagramm (Konzept §14) — kumulativer PnL-Beitrag je Asset bis zur
// Netto-Summe. Dependency-frei (CSS-Balken), pure Geometrie getrennt → testbar.
import { cn } from "@/lib/utils";

export type WaterfallInput = { label: string; value: number };
export type WaterfallBar = {
  label: string;
  value: number;
  /** kumulativer Startwert vor diesem Beitrag. */
  start: number;
  /** kumulativer Endwert nach diesem Beitrag. */
  end: number;
  isTotal: boolean;
};

/** Wandelt Beiträge in Wasserfall-Balken (laufende Summe) + Domain. Pure/testbar.
 *  `withTotal` hängt einen Gesamt-Balken (0→Summe) an. */
export function waterfallBars(
  items: WaterfallInput[],
  withTotal = true,
): { bars: WaterfallBar[]; min: number; max: number } {
  let cum = 0;
  let lo = 0;
  let hi = 0;
  const bars: WaterfallBar[] = items.map((it) => {
    const start = cum;
    cum += it.value;
    const end = cum;
    lo = Math.min(lo, start, end);
    hi = Math.max(hi, start, end);
    return { label: it.label, value: it.value, start, end, isTotal: false };
  });
  if (withTotal) {
    bars.push({ label: "Gesamt", value: cum, start: 0, end: cum, isTotal: true });
    lo = Math.min(lo, 0, cum);
    hi = Math.max(hi, 0, cum);
  }
  return { bars, min: lo, max: hi };
}

export function Waterfall({
  items,
  format = (v) => v.toFixed(0),
  className,
}: {
  items: WaterfallInput[];
  format?: (v: number) => string;
  className?: string;
}) {
  const { bars, min, max } = waterfallBars(items);
  const span = max - min || 1;
  const pct = (v: number) => ((v - min) / span) * 100;

  return (
    <div className={cn("space-y-1", className)}>
      {bars.map((b, i) => {
        const left = Math.min(pct(b.start), pct(b.end));
        const width = Math.max(0.5, Math.abs(pct(b.end) - pct(b.start)));
        const tone = b.isTotal ? "bg-info" : b.value >= 0 ? "bg-pos/70" : "bg-neg/70";
        return (
          <div key={`${b.label}-${i}`} className="flex items-center gap-2">
            <span className="w-24 shrink-0 truncate text-2xs text-fg-muted">{b.label}</span>
            <div className="relative h-4 flex-1 rounded-xs bg-bg-2">
              <div
                className={cn("absolute h-full rounded-xs", tone, b.isTotal && "opacity-90")}
                style={{ left: `${left}%`, width: `${width}%` }}
              />
            </div>
            <span
              className={cn(
                "w-16 text-right font-mono text-2xs",
                b.isTotal ? "font-semibold text-fg" : b.value >= 0 ? "text-pos" : "text-neg",
              )}
            >
              {b.value >= 0 ? "+" : ""}
              {format(b.value)}
            </span>
          </div>
        );
      })}
    </div>
  );
}
