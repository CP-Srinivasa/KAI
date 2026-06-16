// Timeline-/Gantt-Schiene — Trading-Cycles, Roadmap-Phasen, Prozess-Flow
// (Konzept §13/§19). Horizontale getönte Segmente, optional gewichtet. Pure
// Breitenverteilung getrennt.
import { cn } from "@/lib/utils";
import type { Tone } from "@/lib/tone";

export type RailItem = { key: string; label?: string; tone: Tone; weight?: number };

export type RailSegment = RailItem & { widthPct: number };

/** Verteilt Segmente proportional zu `weight` (Default 1 = gleich breit). Pure/testbar. */
export function railSegments(items: RailItem[]): RailSegment[] {
  const total = items.reduce((s, it) => s + (it.weight && it.weight > 0 ? it.weight : 1), 0);
  if (total <= 0) return items.map((it) => ({ ...it, widthPct: 0 }));
  return items.map((it) => {
    const w = it.weight && it.weight > 0 ? it.weight : 1;
    return { ...it, widthPct: (w / total) * 100 };
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

export function TimelineRail({
  items,
  className,
}: {
  items: RailItem[];
  className?: string;
}) {
  const segments = railSegments(items);
  return (
    <div className={cn("flex h-3 w-full overflow-hidden rounded-xs bg-bg-2", className)}>
      {segments.map((s, i) => (
        <div
          key={`${s.key}-${i}`}
          title={s.label ?? s.key}
          className={cn("h-full", TONE_BG[s.tone], i > 0 && "border-l border-bg-1")}
          style={{ width: `${s.widthPct}%` }}
        />
      ))}
    </div>
  );
}
