import type { ReactNode } from "react";
import { Wrench } from "lucide-react";
import { Card, CardHeader, Badge } from "@/components/ui/Primitives";
import { DevelopmentStatus, type DevelopmentPhase } from "@/components/system/DevelopmentStatus";
import { cn } from "@/lib/utils";

// Ehrlicher Platzhalter für Bereiche, die UI-seitig vorbereitet, aber
// backend-seitig noch nicht angebunden sind. Keine erfundenen Werte,
// keine Demo-Zahlen — nur klare Kennzeichnung, was später hier erscheint.
//
// 2026-05-10 DALI-A4: ai-Hairline am Card-Top + ai-tinted Badge/Icon.
// 2026-05-10 DALI-A12: synthwave-pulse-edge ersetzt statische ai-Hairline.
// 2026-05-13 DALI v2 S3 M1d: Optionaler DevelopmentStatus-Top-Strip mit
// phase/progress/timeline (Master-Spec G3 — Operator sieht den Reifegrad).
export function PreparedPanel({
  title,
  reason,
  detail,
  action,
  compact = false,
  phase,
  progress,
  timeline,
}: {
  title: string;
  reason?: string;
  detail?: ReactNode;
  action?: ReactNode;
  compact?: boolean;
  /** DALI v2 S3 M1d: Optional Phase-Anzeige als Top-Strip. */
  phase?: DevelopmentPhase;
  progress?: number;
  timeline?: string;
}) {
  return (
    <Card
      padded={!compact}
      className={cn(
        // synthwave-pulse-edge atmet wie ein Schweif von links nach rechts,
        // gleiche Animation-DNA wie der PageHeader-Divider.
        "synthwave-pulse-edge overflow-hidden",
        compact && "p-4",
      )}
    >
      {phase && (
        <DevelopmentStatus
          phase={phase}
          progress={progress}
          timeline={timeline}
          variant="top-strip"
          className={cn("-mx-5 -mt-5 mb-4", compact && "-mx-4 -mt-4 mb-3")}
        />
      )}
      <CardHeader
        title={title}
        right={
          <Badge tone="ai" dot>
            Integration ausstehend
          </Badge>
        }
      />
      <div className="flex items-start gap-3 text-xs text-fg-muted leading-relaxed">
        <Wrench size={14} className="mt-0.5 text-ai/70 shrink-0" aria-hidden />
        <div className="min-w-0 space-y-1.5">
          <p className="break-words">{reason ?? "Diese Funktion ist UI-seitig vorbereitet, aber noch nicht an das Backend angebunden."}</p>
          {detail && <div className="text-2xs text-fg-subtle break-words">{detail}</div>}
          {action && <div className="pt-1">{action}</div>}
        </div>
      </div>
    </Card>
  );
}
