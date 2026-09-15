import { useState } from "react";
import { ChevronDown, FlaskConical, Shield, Zap, AlertTriangle } from "lucide-react";
import { useAppState, type TradingMode } from "@/state/AppState";
import { useT } from "@/i18n/I18nProvider";
import { StatusDot } from "@/components/ui/Primitives";
import { cn } from "@/lib/utils";

const ICONS: Record<TradingMode, typeof Shield> = {
  paper: Shield,
  live: Zap,
  sim: FlaskConical,
};

// 2026-09-15 DALI v2.1: die Tone-Tabelle ist weg.
//
// Dieser Umschalter faerbte sich rot fuer "live", orange fuer "paper", cyan fuer
// "sim" und pulste im Live-Zustand — er sah aus wie der Hauptschalter der
// Ausfuehrung. Er ist es nicht: `mode` liegt in localStorage
// (state/AppState.tsx), wird an KEINEN Endpunkt geschickt und aendert keinen
// Serverzustand. Die echte Freigabe sind `execution_enabled` /
// `write_back_allowed` aus dem Portfolio-Snapshot; sie stehen im Lage-Streifen
// der Uebersicht und auf der System-Seite.
//
// Bewertet, aber verworfen: den Umschalter zur reinen Anzeige der
// Backend-Wahrheit umbauen. Die Topbar liegt AUSSERHALB des
// PortfolioSnapshotProvider — das haette einen zweiten Poller auf den
// teuersten Endpunkt gesetzt (5,2-MB-Audit je Aufbau, Single-Worker-Pi), fuer
// eine Information, die 40px weiter unten schon steht. Ebenfalls verworfen: die
// Beschriftung "Ansichts-Modus". Er filtert keine Ansicht, er waehlt vor, wofuer
// spaetere Order-Aktionen gelten — "Ansicht" waere die zweite Unwahrheit.
//
// Also: neutrale Optik, kein Puls, ehrliches Label. Die Live-Bestaetigung
// bleibt, denn die Vorwahl ist die Voraussetzung fuer echte Order-Aktionen.

export function ModeSelector({ compact = false }: { compact?: boolean }) {
  const { t } = useT();
  const { mode, setMode, confirmLive } = useAppState();
  const [open, setOpen] = useState(false);
  const [pendingLive, setPendingLive] = useState(false);

  const Icon = ICONS[mode];

  const handlePick = (m: TradingMode) => {
    if (m === "live" && confirmLive && mode !== "live") {
      setPendingLive(true);
      setOpen(false);
      return;
    }
    setMode(m);
    setOpen(false);
  };

  return (
    <>
      <div className="relative">
        <button
          onClick={() => setOpen((v) => !v)}
          className={cn(
            "h-11 lg:h-8 inline-flex items-center gap-1.5 sm:gap-2 rounded-sm border border-line-subtle bg-bg-2 px-2 sm:px-2.5 text-xs text-fg-muted transition-colors hover:bg-bg-3 hover:text-fg",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/70",
            mode === "live" && "border-neg/40 text-neg",
          )}
          aria-haspopup="listbox"
          aria-expanded={open}
          aria-label="Order-Vorwahl wählen — steuert keine laufende Ausführung"
          title="Vorwahl für künftige Order-Aktionen aus dieser Oberfläche. KEIN Systemzustand: die laufende Handelsfreigabe steht im Lage-Streifen der Übersicht (execution_enabled)."
        >
          <StatusDot tone={mode === "live" ? "neg" : "muted"} />
          <Icon size={13} aria-hidden />
          <span className="hidden sm:inline">
            <span className="text-fg-subtle">Vorwahl: </span>
            <span className="font-semibold">
              {mode === "paper" ? t("topbar.mode_paper") : mode === "live" ? t("topbar.mode_live") : t("topbar.mode_sim")}
            </span>
          </span>
          {!compact && <ChevronDown size={12} className="hidden sm:inline opacity-70" />}
        </button>

        {open && (
          <>
            <div className="fixed inset-0 z-30" onClick={() => setOpen(false)} />
            <div
              className={cn(
                "z-40 rounded-md border border-line bg-bg-1 shadow-raised p-1",
                "fixed top-[3.875rem] inset-x-2 w-auto",
                "sm:absolute sm:top-full sm:mt-1.5 sm:inset-x-auto sm:right-0 sm:w-[260px]",
              )}
            >
              <div className="px-2 py-1.5 text-2xs font-semibold uppercase tracking-[0.08em] text-fg-subtle">
                {t("topbar.mode_switch")}
              </div>
              <p className="px-2 pb-1.5 text-2xs leading-relaxed text-fg-muted">
                Vorwahl für Order-Aktionen aus dieser Oberfläche. Sie schaltet
                nichts im Backend — ob KAI ausführt, steht im Lage-Streifen der
                Übersicht.
              </p>
              {(["paper", "sim", "live"] as TradingMode[]).map((m) => {
                const I = ICONS[m];
                const active = m === mode;
                return (
                  <button
                    key={m}
                    onClick={() => handlePick(m)}
                    className={cn(
                      "w-full min-h-[44px] flex items-start gap-2.5 p-2 rounded-sm text-left transition-colors",
                      "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/70",
                      active ? "bg-bg-3" : "hover:bg-bg-2",
                    )}
                  >
                    <span
                      className={cn(
                        "h-7 w-7 rounded-sm grid place-items-center shrink-0 mt-0.5",
                        // Nur "live" behaelt eine Warnfarbe — dort haengt eine
                        // echte Konsequenz dran. paper/sim sind neutral.
                        m === "live" ? "bg-neg/10 text-neg" : "bg-bg-3 text-fg-muted",
                      )}
                    >
                      <I size={13} />
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-1.5">
                        <span className="text-xs font-semibold text-fg">
                          {m === "paper" ? t("topbar.mode_paper") : m === "live" ? t("topbar.mode_live") : t("topbar.mode_sim")}
                        </span>
                        {active && (
                          <span className="text-[10px] font-mono text-fg-subtle uppercase tracking-wider">
                            {t("topbar.mode_current")}
                          </span>
                        )}
                      </div>
                      <p className="mt-0.5 text-2xs text-fg-muted leading-relaxed">
                        {m === "paper"
                          ? t("pages.external.risk_notes.paper_mode")
                          : m === "live"
                            ? t("topbar.mode_warning_live")
                            : t("topbar.mode_warning_sim")}
                      </p>
                    </div>
                  </button>
                );
              })}
            </div>
          </>
        )}
      </div>

      {pendingLive && (
        <ConfirmLiveDialog
          onCancel={() => setPendingLive(false)}
          onConfirm={() => {
            setMode("live");
            setPendingLive(false);
          }}
        />
      )}
    </>
  );
}

function ConfirmLiveDialog({ onCancel, onConfirm }: { onCancel: () => void; onConfirm: () => void }) {
  const { t } = useT();
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/50 backdrop-blur-[2px] p-6">
      <div className="w-full max-w-md rounded-lg border border-neg/30 bg-bg-1 shadow-raised">
        <div className="p-5 border-b border-line-subtle flex items-start gap-3">
          <div className="h-9 w-9 rounded-md bg-neg/10 text-neg grid place-items-center">
            <AlertTriangle size={16} />
          </div>
          <div>
            <h3 className="text-sm font-semibold text-fg">{t("topbar.mode_live")} aktivieren?</h3>
            <p className="mt-1 text-xs text-fg-muted">{t("topbar.mode_warning_live")}</p>
          </div>
        </div>
        <div className="px-5 py-4 text-xs text-fg-muted space-y-2 bg-bg-2">
          <div className="flex items-start gap-2">
            <span className="h-1.5 w-1.5 rounded-full bg-neg mt-1.5 shrink-0" />
            <span>Alle Order-Aktionen treffen echte Orders an der angebundenen Börse.</span>
          </div>
          <div className="flex items-start gap-2">
            <span className="h-1.5 w-1.5 rounded-full bg-neg mt-1.5 shrink-0" />
            <span>Externe Signale werden nach Bestätigung ausgeführt.</span>
          </div>
          <div className="flex items-start gap-2">
            <span className="h-1.5 w-1.5 rounded-full bg-neg mt-1.5 shrink-0" />
            <span>Risiko-Cap und Cooldown aus den Einstellungen gelten weiterhin.</span>
          </div>
        </div>
        <div className="p-4 flex justify-end gap-2">
          <button
            onClick={onCancel}
            className="h-8 px-3 rounded-sm border border-line bg-bg-2 text-xs font-medium text-fg hover:bg-bg-3"
          >
            {t("common.cancel")}
          </button>
          <button
            onClick={onConfirm}
            className="h-8 px-3 rounded-sm bg-neg text-white text-xs font-semibold hover:bg-neg/90"
          >
            {t("topbar.mode_live")} aktivieren
          </button>
        </div>
      </div>
    </div>
  );
}
