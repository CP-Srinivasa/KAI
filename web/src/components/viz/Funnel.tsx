// Trichterdiagramm — Delivery-Funnel (Alerts), Signal→Fill-Funnel (Konzept §15/§17).
// Reine CSS-Balken (kein SVG nötig). Pure Breitenberechnung getrennt.
import { cn } from "@/lib/utils";
import type { Tone } from "@/lib/tone";

export type FunnelStage = { label: string; value: number; tone?: Tone };

export type FunnelRow = FunnelStage & {
  /** Breite relativ zur ersten (größten) Stufe, 0..100. */
  widthPct: number;
  /** Erhaltungsrate ggü. der vorigen Stufe, 0..1 (1. Stufe = 1). */
  retention: number;
};

/** Funnel-Stufen → Breiten/Retention. Pure/testbar. Breite ist relativ zur
 *  ersten Stufe (Trichter-Konvention); leere/0-Top-Stufe → alle 0. */
export function funnelRows(stages: FunnelStage[]): FunnelRow[] {
  if (stages.length === 0) return [];
  const top = stages[0].value;
  let prev = top;
  return stages.map((s, i) => {
    const widthPct = top > 0 ? Math.max(0, Math.min(100, (s.value / top) * 100)) : 0;
    const retention = i === 0 ? 1 : prev > 0 ? s.value / prev : 0;
    prev = s.value;
    return { ...s, widthPct, retention };
  });
}

const TONE_BG: Record<Tone, string> = {
  pos: "bg-pos/70",
  neg: "bg-neg/70",
  warn: "bg-warn/70",
  info: "bg-info/70",
  ai: "bg-ai/70",
  neutral: "bg-fg-muted/40",
};

export function Funnel({ stages, className }: { stages: FunnelStage[]; className?: string }) {
  const rows = funnelRows(stages);
  return (
    <div className={cn("space-y-1", className)}>
      {rows.map((r, i) => (
        <div key={`${r.label}-${i}`} className="flex items-center gap-2">
          <span className="w-28 shrink-0 truncate text-2xs text-fg-muted">{r.label}</span>
          <div className="relative h-4 flex-1 rounded-xs bg-bg-2">
            <div
              className={cn("h-full rounded-xs", TONE_BG[r.tone ?? "info"])}
              style={{ width: `${r.widthPct}%` }}
            />
            <span className="absolute inset-y-0 right-1 flex items-center font-mono text-2xs text-fg">
              {r.value}
              {i > 0 && (
                <span className="ml-1 text-fg-subtle">{(r.retention * 100).toFixed(0)}%</span>
              )}
            </span>
          </div>
        </div>
      ))}
    </div>
  );
}
