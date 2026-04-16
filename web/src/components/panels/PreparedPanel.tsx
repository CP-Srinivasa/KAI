import type { ReactNode } from "react";
import { Wrench } from "lucide-react";
import { Card, CardHeader, Badge } from "@/components/ui/Primitives";

// Ehrlicher Platzhalter für Bereiche, die UI-seitig vorbereitet, aber
// backend-seitig noch nicht angebunden sind. Keine erfundenen Werte,
// keine Demo-Zahlen — nur klare Kennzeichnung, was später hier erscheint.
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
    <Card padded={!compact} className={compact ? "p-4" : undefined}>
      <CardHeader
        title={title}
        right={
          <Badge tone="muted" dot>
            Integration ausstehend
          </Badge>
        }
      />
      <div className="flex items-start gap-3 text-xs text-fg-muted leading-relaxed">
        <Wrench size={14} className="mt-0.5 text-fg-subtle shrink-0" aria-hidden />
        <div className="min-w-0 space-y-1.5">
          <p>{reason ?? "Diese Funktion ist UI-seitig vorbereitet, aber noch nicht an das Backend angebunden."}</p>
          {detail && <div className="text-2xs text-fg-subtle">{detail}</div>}
          {action && <div className="pt-1">{action}</div>}
        </div>
      </div>
    </Card>
  );
}
