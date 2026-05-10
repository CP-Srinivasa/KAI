import type { ReactNode } from "react";
import { Wrench } from "lucide-react";
import { Card, CardHeader, Badge } from "@/components/ui/Primitives";
import { cn } from "@/lib/utils";

// Ehrlicher Platzhalter für Bereiche, die UI-seitig vorbereitet, aber
// backend-seitig noch nicht angebunden sind. Keine erfundenen Werte,
// keine Demo-Zahlen — nur klare Kennzeichnung, was später hier erscheint.
//
// 2026-05-10 DALI-A4: ai-Hairline am Card-Top + ai-tinted Badge/Icon.
// Vorher dominierte ein muted-grauer Look auf 6 Sub-Pages, der "kaputt"
// statt "vorbereitet" wirkte. Hairline = visuelles Pendant zur synthwave-edge
// im KAI-Live-Widget, signalisiert "KAI denkt schon an diesen Bereich".
export function PreparedPanel({
  title,
  reason,
  detail,
  action,
  compact = false,
}: {
  title: string;
  reason?: string;
  detail?: ReactNode;
  action?: ReactNode;
  compact?: boolean;
}) {
  return (
    <Card
      padded={!compact}
      className={cn(
        "relative overflow-hidden",
        "before:absolute before:top-0 before:left-4 before:right-4 before:h-px",
        "before:bg-gradient-to-r before:from-transparent before:via-ai/40 before:to-transparent",
        compact && "p-4",
      )}
    >
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
