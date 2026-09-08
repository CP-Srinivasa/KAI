import { AlertCircle, Loader2, RefreshCw } from "lucide-react";

import { Card } from "@/components/ui/Primitives";

// 2026-09-08: Eine Panel-Zustandsflaeche statt bisher ueber 40 einzelner
// "nicht erreichbar"-Meldungen in ueber 25 Dateien — von denen genau EINE einen
// Wiederholen-Knopf hatte. Zwei Seiten (Risk, AI Insights) rendern bei einem
// Fehler ueberhaupt nichts und bleiben stumm leer.
//
// Zwei Regeln, die hier bewusst durchgesetzt werden:
//  1. Kein Zustand ist eine Sackgasse — Fehler traegt immer ein `onRetry`.
//  2. Die technische Kennung erklaert nichts. Zuerst steht der Klartext-Grund,
//     Code/Pfad/Request-ID stehen darunter fuer die Diagnose. Ohne die
//     Normalisierung in lib/api.ts stand hier "[object Object]".

export type PanelErrorInfo = {
  kind: string;
  message: string;
  code?: string | null;
  requestId?: string | null;
  path?: string | null;
};

/** Klartext zuerst — der Operator soll ohne Vorwissen wissen, woran er ist. */
function humanReason(err: PanelErrorInfo): string {
  switch (err.kind) {
    case "timeout":
      return "Das Backend hat nicht rechtzeitig geantwortet.";
    case "network":
      return "Keine Verbindung zum Backend.";
    case "unauthorized":
    case "forbidden":
      return "Die Sitzung ist abgelaufen oder nicht berechtigt — neu anmelden.";
    case "rate_limited":
      // Der Riegel sperrt pro Client-IP fuer 300 s; ein Wiederholen verlaengert ihn.
      return "Zu viele Anfragen — die Sperre laeuft nach einigen Minuten von selbst ab.";
    case "not_found":
      return "Dieser Endpunkt existiert nicht (mehr).";
    case "server":
      return "Das Backend meldet einen Fehler.";
    default:
      return "Daten konnten nicht geladen werden.";
  }
}

export function PanelLoading({ label = "lädt …" }: { label?: string }) {
  return (
    <Card padded>
      <div className="text-fg-muted flex items-center gap-2 text-xs">
        <Loader2 size={14} className="animate-spin shrink-0" aria-hidden />
        <span>{label}</span>
      </div>
    </Card>
  );
}

export function PanelError({
  error,
  onRetry,
  title = "Daten nicht verfügbar",
}: {
  error: PanelErrorInfo;
  onRetry?: () => void;
  title?: string;
}) {
  // Ausfall ist nicht Risiko: gedaempft statt alarmrot, damit Rot der echten
  // Risikolage vorbehalten bleibt und keine Alarmermuedung entsteht.
  const detail = [error.code, error.requestId].filter(Boolean).join(" · ");
  return (
    <Card padded className="border-fg-subtle/25">
      <div className="text-fg-muted flex items-start gap-3 text-xs">
        <AlertCircle size={16} className="mt-0.5 shrink-0" aria-hidden />
        <div className="min-w-0 flex-1">
          <div className="text-fg font-semibold">{title}</div>
          <div className="mt-1 break-words">{humanReason(error)}</div>
          {error.message && (
            <div className="text-2xs text-fg-subtle mt-1 break-words">{error.message}</div>
          )}
          {(detail || error.path) && (
            <div className="text-2xs text-fg-subtle mt-1 truncate font-mono" title={error.path ?? undefined}>
              {[detail, error.path].filter(Boolean).join(" · ")}
            </div>
          )}
        </div>
        {onRetry && (
          <button
            type="button"
            onClick={onRetry}
            className="border-fg-subtle/30 text-fg-muted hover:text-fg hover:border-fg-subtle/60 inline-flex shrink-0 items-center gap-1.5 rounded-md border px-2 py-1 text-xs transition-colors"
          >
            <RefreshCw size={12} aria-hidden />
            Erneut laden
          </button>
        )}
      </div>
    </Card>
  );
}
