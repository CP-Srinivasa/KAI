import { useEffect, useId, useRef, useState } from "react";
import { useBackendHealth, type BackendStatus } from "@/lib/useBackendHealth";
import { cn } from "@/lib/utils";

// Backend-Zustand als kompakte Pille in der Topbar — auf ALLEN Seiten
// (SP-8 Teil 2, Operator-Entscheid 17.09.). Ersetzt den BackendStatusBanner
// ohne Informationsverlust: Im gesunden Zustand steht nur "Backend ok"; die
// Ursache einer Stoerung (Offline-Grund, Anmeldefehler mit Neu-laden-Hinweis)
// steht im Tooltip UND im aufklappbaren Detail. Die Version gehoert in den
// Footer. Gleiche Quelle wie vorher (EIN geteilter /health-Poller), kein neuer
// Abruf. Auth laeuft ueber Cloudflare Access (vor dem Tunnel).

type View = { short: string; full: string; dot: string; text: string };

function describe(s: BackendStatus): View {
  switch (s.state) {
    case "connected":
      return { short: "Backend ok", full: "Backend verbunden", dot: "bg-pos", text: "text-fg-muted" };
    case "unauthorized":
      // Sollte mit CF Access nicht mehr auftreten — /health ist oeffentlich.
      // Falls doch: CF-Access-Session abgelaufen -> reload erzwingt Re-Login.
      return {
        short: "Backend: Anmeldung",
        full: "Backend erreichbar, aber Auth fehlgeschlagen — Seite neu laden",
        dot: "bg-warn",
        text: "text-warn",
      };
    case "offline":
      return {
        short: "Backend offline",
        full: `Backend offline · ${s.detail ?? ""}`.trim(),
        dot: "bg-neg",
        text: "text-neg",
      };
    case "checking":
    default:
      return { short: "Backend prüft …", full: "Backend wird geprüft …", dot: "bg-fg-subtle", text: "text-fg-muted" };
  }
}

export function BackendStatusPill() {
  const s = useBackendHealth();
  const v = describe(s);
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const panelId = useId();
  // Beim Anmeldefehler ist die technische Meldung eine zusaetzliche Ursache,
  // die der Banner nicht zeigte; beim Offline-Fall steckt sie schon in `full`.
  const extra = s.state === "unauthorized" ? s.detail : null;

  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("mousedown", onClick);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", onClick);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div className="relative" ref={rootRef}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        title={extra ? `${v.full} (${extra})` : v.full}
        aria-label={`${v.short} — Details`}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        className={cn(
          "h-11 min-w-[44px] lg:h-8 lg:min-w-0 xl:min-w-8 2xl:min-w-0 inline-flex items-center justify-center gap-1.5 rounded-sm border border-line-subtle bg-bg-2 px-2 text-2xs font-medium hover:bg-bg-3 transition-colors",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/70",
          v.text,
        )}
      >
        <span className={cn("inline-block h-1.5 w-1.5 rounded-full shrink-0", v.dot)} aria-hidden />
        {/* Auf schmalen Schirmen nur der Punkt; Text in Tooltip und Detail.
            Ebenso zwischen xl und 2xl: dort blendet die Topbar alle Steuerelemente
            ein, und mit Text lief die Leiste bei 1280-1535 px ueber den Rand
            (CDP-Messung 17.09.). */}
        <span className="hidden sm:inline xl:hidden 2xl:inline font-mono whitespace-nowrap">{v.short}</span>
      </button>

      {/* Live-Region wie beim Banner: Zustandswechsel werden angesagt. */}
      <span className="sr-only" role="status" aria-live="polite">
        {v.full}
      </span>

      {open && (
        <div
          id={panelId}
          role="dialog"
          aria-label="Backend-Zustand"
          className="absolute right-0 top-full mt-1.5 z-40 w-[min(320px,calc(100vw-1rem))] rounded-md border border-line bg-bg-1 shadow-raised p-3 space-y-1.5 text-xs"
        >
          <div className="text-2xs font-semibold uppercase tracking-[0.08em] text-fg-subtle">Backend-Zustand</div>
          <p className={cn("font-mono break-words", v.text)}>{v.full}</p>
          {extra && <p className="font-mono text-2xs text-fg-subtle break-words">Ursache: {extra}</p>}
          <p className="text-2xs text-fg-subtle">Quelle: /health, alle 30 s</p>
        </div>
      )}
    </div>
  );
}
