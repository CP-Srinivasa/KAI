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

const TONE: Record<TradingMode, "warn" | "neg" | "info"> = {
  paper: "warn",
  live: "neg",
  sim: "info",
};

export function ModeSelector({ compact = false }: { compact?: boolean }) {
  const { t } = useT();
  const { mode, setMode, confirmLive } = useAppState();
  const [open, setOpen] = useState(false);
  const [pendingLive, setPendingLive] = useState(false);

  const Icon = ICONS[mode];
  const tone = TONE[mode];

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
            "h-8 inline-flex items-center gap-2 rounded-sm border bg-bg-2 px-2.5 text-xs transition-colors",
            tone === "neg"
              ? "border-neg/40 text-neg bg-neg/5 hover:bg-neg/10"
              : tone === "info"
                ? "border-info/30 text-info bg-info/5 hover:bg-info/10"
                : "border-warn/30 text-warn bg-warn/5 hover:bg-warn/10",
          )}
          aria-haspopup="listbox"
          aria-expanded={open}
        >
          <StatusDot tone={tone} pulse={mode === "live"} />
          <Icon size={13} />
          <span className="font-semibold">
            {mode === "paper" ? t("topbar.mode_paper") : mode === "live" ? t("topbar.mode_live") : t("topbar.mode_sim")}
          </span>
          {!compact && <ChevronDown size={12} className="opacity-70" />}
        </button>

        {open && (
          <>
            <div className="fixed inset-0 z-30" onClick={() => setOpen(false)} />
            <div className="absolute right-0 top-full mt-1.5 z-40 w-[260px] rounded-md border border-line bg-bg-1 shadow-raised p-1">
              <div className="px-2 py-1.5 text-2xs font-semibold uppercase tracking-[0.08em] text-fg-subtle">
                {t("topbar.mode_switch")}
              </div>
              {(["paper", "sim", "live"] as TradingMode[]).map((m) => {
                const I = ICONS[m];
                const active = m === mode;
                const toneM = TONE[m];
                return (
                  <button
                    key={m}
                    onClick={() => handlePick(m)}
                    className={cn(
                      "w-full flex items-start gap-2.5 p-2 rounded-sm text-left transition-colors",
                      active ? "bg-bg-3" : "hover:bg-bg-2",
                    )}
                  >
                    <span
                      className={cn(
                        "h-7 w-7 rounded-sm grid place-items-center shrink-0 mt-0.5",
                        toneM === "neg" && "bg-neg/10 text-neg",
                        toneM === "info" && "bg-info/10 text-info",
                        toneM === "warn" && "bg-warn/10 text-warn",
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
