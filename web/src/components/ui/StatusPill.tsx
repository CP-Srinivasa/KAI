// <StatusPill> — die eine wiederverwendbare Anzeige für die Status-Sprache-SSOT
// (WP-0.1 / Konzept §22). Rendert einen Zustand aus lib/status.ts konsistent als
// Badge: semantische Farbe + Icon (Bedeutung auch ohne Farbe — A11y) + Label, mit
// Tooltip ("Was bedeutet dieser Zustand?" + empfohlene Aktion) im title-Attribut.
//
// Bewusst dünn: keine eigene Tönung, keine Ad-hoc-Strings — alles kommt aus der
// Registry, damit jeder Status app-weit identisch aussieht und bedeutet.

import { Badge } from "@/components/ui/Primitives";
import { getStatus, type StatusKind } from "@/lib/status";

export function StatusPill({
  kind,
  label,
  dot = true,
  showIcon = true,
  className,
}: {
  kind: StatusKind;
  /** Überschreibt den Default-Label-Text (z.B. mit konkretem Wert "3 / 24h"). */
  label?: string;
  dot?: boolean;
  showIcon?: boolean;
  className?: string;
}) {
  const d = getStatus(kind);
  const Icon = d.icon;
  const title = d.action ? `${d.tooltip} · Aktion: ${d.action}` : d.tooltip;
  return (
    <Badge tone={d.tone} dot={dot} title={title} className={className}>
      {showIcon && <Icon size={10} aria-hidden />}
      {label ?? d.label}
    </Badge>
  );
}
