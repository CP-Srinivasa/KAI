// Halbkreis-Gauge (Pegelkarte) — Risk-Meter, Klumpenrisiko, Auslastung (Konzept §14/§16).
// Dependency-frei (SVG). Pure Geometrie getrennt (viz.test.ts).
import { cn } from "@/lib/utils";
import type { Tone } from "@/lib/tone";
import { toneText } from "@/components/viz/colors";

const R = 42; // Radius im 100×56-ViewBox
const CX = 50;
const CY = 50;
const SEMI_LEN = Math.PI * R; // Bogenlänge des Halbkreises

/** Clamp eines Werts auf den Anteil 0..1 innerhalb [min,max]. Pure/testbar. */
export function gaugeFraction(value: number, min = 0, max = 1): number {
  if (!Number.isFinite(value) || max <= min) return 0;
  return Math.max(0, Math.min(1, (value - min) / (max - min)));
}

// Statischer Halbkreis-Pfad (von links nach rechts, oben gewölbt).
const ARC_PATH = `M ${CX - R} ${CY} A ${R} ${R} 0 0 1 ${CX + R} ${CY}`;

export function Gauge({
  value,
  min = 0,
  max = 1,
  tone = "info",
  label,
  className,
}: {
  value: number | null | undefined;
  min?: number;
  max?: number;
  tone?: Tone;
  /** Optionaler Zentrums-Text (z.B. "72%"). */
  label?: string;
  className?: string;
}) {
  const frac = value == null ? 0 : gaugeFraction(value, min, max);
  const filled = SEMI_LEN * frac;
  return (
    <div className={cn("relative inline-block", className)}>
      <svg viewBox="0 0 100 56" width="100%" height="100%" aria-hidden>
        <path
          d={ARC_PATH}
          fill="none"
          className="text-line-subtle"
          stroke="currentColor"
          strokeWidth={8}
          strokeLinecap="round"
        />
        <path
          d={ARC_PATH}
          fill="none"
          className={toneText(tone)}
          stroke="currentColor"
          strokeWidth={8}
          strokeLinecap="round"
          strokeDasharray={`${filled.toFixed(2)} ${SEMI_LEN.toFixed(2)}`}
          vectorEffect="non-scaling-stroke"
        />
      </svg>
      {label != null && (
        <span className="absolute inset-x-0 bottom-0 text-center font-mono text-sm font-semibold text-fg">
          {label}
        </span>
      )}
    </div>
  );
}
