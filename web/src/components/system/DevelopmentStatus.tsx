import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

/* DALI Dashboard v2 - Master-Spec G3: Entwicklungsstatus
   Sichtbarer Marker fuer unfertige Module. Der Operator sieht sofort:
   - In welcher Phase steckt diese Card?
   - Wie weit ist sie?
   - Wann ist sie geplant?
   - Was bedeutet das fuer ihn JETZT?

   Verwendet bestehende Synthwave-Toene + Progress-Pattern. Keine neuen
   npm-Deps, keine externen Progress-Libs. */

export type DevelopmentPhase = "planning" | "skeleton" | "beta" | "stable";

const PHASE_LABEL: Record<DevelopmentPhase, string> = {
  planning: "Planung",
  skeleton: "Skeleton",
  beta: "Beta",
  stable: "Stabil",
};

const PHASE_DESC: Record<DevelopmentPhase, string> = {
  planning:
    "Architektur in Klaerung. UI zeigt vorbereitete Strukturen, noch keine echten Daten.",
  skeleton: "Erstes Geruest steht. Funktionen werden schrittweise aktiv.",
  beta: "Funktional, in Operator-Tests. Kleinere Abweichungen moeglich.",
  stable: "Produktiv. Regulaerer Betrieb, Daten sind verlaesslich.",
};

const PHASE_TONE: Record<DevelopmentPhase, { border: string; bar: string; text: string }> = {
  planning: { border: "border-ai/40", bar: "bg-ai", text: "text-ai" },
  skeleton: { border: "border-info/40", bar: "bg-info", text: "text-info" },
  beta: { border: "border-warn/40", bar: "bg-warn", text: "text-warn" },
  stable: { border: "border-pos/40", bar: "bg-pos", text: "text-pos" },
};

const PHASE_PROGRESS_DEFAULT: Record<DevelopmentPhase, number> = {
  planning: 15,
  skeleton: 40,
  beta: 75,
  stable: 100,
};

type Props = {
  phase: DevelopmentPhase;
  /** 0-100. Wenn nicht angegeben: Default-Mapping pro Phase. */
  progress?: number;
  /** Klartext wann/wie es weitergeht. Z.B. "Aktiv ab Sprint S4". */
  timeline?: string;
  /** Zusaetzliche Klartext-Erklaerung (zeigt warum gerade so). */
  note?: string;
  /** Layout-Variant. */
  variant?: "card-header" | "top-strip" | "inline";
  className?: string;
};

export function DevelopmentStatus({
  phase,
  progress,
  timeline,
  note,
  variant = "card-header",
  className,
}: Props): ReactNode {
  const tone = PHASE_TONE[phase];
  const pct = clamp(progress ?? PHASE_PROGRESS_DEFAULT[phase]);
  const label = PHASE_LABEL[phase];
  const desc = note ?? PHASE_DESC[phase];

  if (variant === "inline") {
    return (
      <span
        role="status"
        title={`${label} - ${desc}`}
        className={cn(
          "inline-flex items-center gap-1.5 h-5 px-1.5 rounded-sm border bg-bg-2 text-2xs font-mono uppercase tracking-wider",
          tone.border,
          tone.text,
          className,
        )}
      >
        <span aria-hidden className={cn("inline-block w-1.5 h-1.5 rounded-full", tone.bar)} />
        <span>{label}</span>
        <span className="text-fg-subtle">{pct}%</span>
      </span>
    );
  }

  if (variant === "top-strip") {
    return (
      <div
        role="status"
        aria-label={`Entwicklungsstatus: ${label}, ${pct} Prozent. ${desc}`}
        className={cn(
          "relative w-full rounded-t-md border-b bg-bg-2 px-3 py-1.5",
          tone.border,
          className,
        )}
      >
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <div className="flex items-center gap-2 min-w-0">
            <span aria-hidden className={cn("inline-block w-2 h-2 rounded-full shrink-0", tone.bar)} />
            <span className={cn("text-2xs font-mono uppercase tracking-wider", tone.text)}>
              {label}
            </span>
            <span className="text-2xs text-fg-subtle">{pct}%</span>
            {timeline && (
              <span className="text-2xs text-fg-muted truncate">- {timeline}</span>
            )}
          </div>
          {desc && (
            <span className="text-2xs text-fg-muted leading-snug min-w-0 truncate">{desc}</span>
          )}
        </div>
        <ProgressLine pct={pct} barClass={tone.bar} />
      </div>
    );
  }

  // variant === "card-header" (Default)
  return (
    <div
      role="status"
      aria-label={`Entwicklungsstatus: ${label}, ${pct} Prozent. ${desc}`}
      className={cn(
        "rounded-md border bg-bg-2 px-3 py-2 space-y-2",
        tone.border,
        className,
      )}
    >
      <div className="flex items-center gap-2 flex-wrap">
        <span aria-hidden className={cn("inline-block w-2 h-2 rounded-full shrink-0", tone.bar)} />
        <span className={cn("text-2xs font-mono font-semibold uppercase tracking-wider", tone.text)}>
          {label}
        </span>
        <span className="text-2xs text-fg-subtle">{pct}%</span>
        {timeline && (
          <span className="text-2xs text-fg-muted ml-auto truncate">{timeline}</span>
        )}
      </div>
      <ProgressLine pct={pct} barClass={tone.bar} />
      {desc && (
        <p className="text-2xs text-fg-muted leading-relaxed break-words">{desc}</p>
      )}
    </div>
  );
}

function ProgressLine({ pct, barClass }: { pct: number; barClass: string }) {
  return (
    <div className="h-1 w-full rounded-full bg-bg-3 overflow-hidden">
      <div
        aria-hidden
        className={cn("h-full rounded-full transition-[width] duration-500", barClass)}
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}

function clamp(n: number): number {
  if (!Number.isFinite(n)) return 0;
  if (n < 0) return 0;
  if (n > 100) return 100;
  return Math.round(n);
}
