// @data-source: props (parent-provided)
import type { ReactNode } from "react";
import { Wrench } from "lucide-react";
import { Card, CardHeader, Badge } from "@/components/ui/Primitives";
import { cn } from "@/lib/utils";

// Ehrlicher Platzhalter für Bereiche, die UI-seitig vorbereitet, aber noch nicht
// an eine Live-Datenquelle angebunden sind. KEINE erfundenen Werte, KEINE
// Demo-Zahlen und — bewusst — KEINE irreführenden Prozent-/Phasen-/„halbfertig"-
// Balken mehr. Ein einziges klares Status-Badge sagt dem Operator, was die Karte
// wirklich ist (Goal 2026-06-03: Dashboard-Karten ehrlich auflösen).
export type PreparedStatus =
  | "roadmap" // geplant, noch kein Live-Datenpfad
  | "paper_only" // nur im Paper-Modus sinnvoll
  | "live_only" // braucht Live-Trading/echte Konten — bewusst nicht aktiv
  | "no_data" // Pfad existiert, aktuell aber keine Daten
  | "unavailable"; // Backend/Provider nicht erreichbar

const STATUS_LABEL: Record<PreparedStatus, string> = {
  roadmap: "Roadmap",
  paper_only: "Paper-Mode",
  live_only: "Live-only",
  no_data: "Keine Daten",
  unavailable: "Backend nicht erreichbar",
};

const STATUS_TONE: Record<PreparedStatus, "ai" | "info" | "warn" | "neg"> = {
  roadmap: "ai",
  paper_only: "info",
  live_only: "info",
  no_data: "warn",
  unavailable: "neg",
};

export function PreparedPanel({
  title,
  reason,
  detail,
  action,
  compact = false,
  status = "roadmap",
  roadmapNote,
  // Legacy-Props: bewusst akzeptiert, aber NICHT mehr gerendert. Sie trugen
  // früher den irreführenden Prozent-/Phasen-Strip. `timeline` wird, falls
  // gesetzt, als ehrliche Roadmap-Notiz (ohne Prozent) weitergeführt.
  phase: _phase,
  progress: _progress,
  timeline,
}: {
  title: string;
  reason?: string;
  detail?: ReactNode;
  action?: ReactNode;
  compact?: boolean;
  status?: PreparedStatus;
  roadmapNote?: string;
  /** @deprecated entfällt — kein Reifegrad-Prozent mehr. */
  phase?: string;
  /** @deprecated entfällt — kein Reifegrad-Prozent mehr. */
  progress?: number;
  /** @deprecated als `roadmapNote` weitergeführt. */
  timeline?: string;
}) {
  const label = STATUS_LABEL[status];
  const tone = STATUS_TONE[status];
  const note = roadmapNote ?? timeline;
  return (
    <Card
      padded={!compact}
      className={cn("synthwave-pulse-edge overflow-hidden", compact && "p-4")}
    >
      <CardHeader
        title={title}
        right={
          <Badge tone={tone} dot>
            {label}
          </Badge>
        }
      />
      <div className="flex items-start gap-3 text-xs text-fg-muted leading-relaxed">
        <Wrench size={14} className="mt-0.5 text-ai/70 shrink-0" aria-hidden />
        <div className="min-w-0 space-y-1.5">
          <p className="break-words">
            {reason ??
              "Diese Funktion ist UI-seitig vorbereitet, aber noch nicht an eine Live-Datenquelle angebunden."}
          </p>
          {detail && <div className="text-2xs text-fg-subtle break-words">{detail}</div>}
          {note && <div className="text-2xs text-fg-subtle break-words">{note}</div>}
          {action && <div className="pt-1">{action}</div>}
        </div>
      </div>
    </Card>
  );
}
