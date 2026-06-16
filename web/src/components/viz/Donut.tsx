// Donut/Allocation-Ring — Portfolio-Allocation, Anteilsverteilung (Konzept §14).
// Dependency-frei (SVG, stroke-dasharray-Segmente). Pure Segmentierung getrennt.
import { cn } from "@/lib/utils";
import type { Tone } from "@/lib/tone";
import { toneText } from "@/components/viz/colors";

const R = 40;
const C = 2 * Math.PI * R; // Umfang

export type DonutDatum = { label: string; value: number; tone: Tone };

export type DonutSegment = {
  label: string;
  tone: Tone;
  fraction: number;
  /** stroke-dasharray: sichtbarer Bogen + Rest. */
  dashArray: string;
  /** stroke-dashoffset: Start des Segments (negativ = im Uhrzeigersinn). */
  dashOffset: number;
};

/** Wandelt Werte in Ring-Segmente (Anteile, dash-Geometrie). Pure/testbar.
 *  Nicht-positive Werte und Gesamtsumme 0 werden sicher behandelt. */
export function donutSegments(data: DonutDatum[]): DonutSegment[] {
  const clean = data.filter((d) => Number.isFinite(d.value) && d.value > 0);
  const total = clean.reduce((s, d) => s + d.value, 0);
  if (total <= 0) return [];
  let cumulative = 0;
  return clean.map((d) => {
    const fraction = d.value / total;
    const seg: DonutSegment = {
      label: d.label,
      tone: d.tone,
      fraction,
      dashArray: `${(fraction * C).toFixed(2)} ${(C - fraction * C).toFixed(2)}`,
      dashOffset: cumulative > 0 ? -(cumulative * C) : 0,
    };
    cumulative += fraction;
    return seg;
  });
}

export function Donut({
  data,
  centerLabel,
  className,
}: {
  data: DonutDatum[];
  centerLabel?: string;
  className?: string;
}) {
  const segments = donutSegments(data);
  return (
    <div className={cn("relative inline-block", className)}>
      <svg viewBox="0 0 100 100" width="100%" height="100%" aria-hidden>
        <circle
          cx={50}
          cy={50}
          r={R}
          fill="none"
          className="text-line-subtle"
          stroke="currentColor"
          strokeWidth={12}
        />
        {segments.map((s, i) => (
          <circle
            key={`${s.label}-${i}`}
            cx={50}
            cy={50}
            r={R}
            fill="none"
            className={toneText(s.tone)}
            stroke="currentColor"
            strokeWidth={12}
            strokeDasharray={s.dashArray}
            strokeDashoffset={s.dashOffset}
            transform="rotate(-90 50 50)"
          />
        ))}
      </svg>
      {centerLabel != null && (
        <span className="absolute inset-0 flex items-center justify-center font-mono text-sm font-semibold text-fg">
          {centerLabel}
        </span>
      )}
    </div>
  );
}
