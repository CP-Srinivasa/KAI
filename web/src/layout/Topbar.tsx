import { Moon, Search, Sun, Languages, Menu, MoreHorizontal, Rows3, Rows2, X } from "lucide-react";
import { useState } from "react";
import { useTheme } from "@/theme/ThemeProvider";
import { ModeSelector } from "@/components/trading/ModeSelector";
import { useT } from "@/i18n/I18nProvider";
import { useCurrency, type Currency } from "@/state/CurrencyProvider";
import { useAppState, TIMEFRAMES, nextDensity } from "@/state/AppState";
import { useRouter, type Route } from "@/state/Router";
import { cn } from "@/lib/utils";
import { NotificationsBell } from "./NotificationsBell";
import { BackendStatusPill } from "./BackendStatusPill";

const CONTEXT: Record<Route, string> = {
  dashboard: "nav.dashboard",
  signals: "nav.signals",
  trades: "nav.trades",
  portfolio: "nav.portfolio",
  risk: "nav.risk",
  ai: "nav.ai",
  alerts: "nav.alerts",
  external: "nav.external",
  sources: "nav.sources",
  node: "nav.node",
  pay: "nav.pay",
  agents: "nav.agents",
  roadmaps: "nav.roadmaps",
  system: "nav.system",
  settings: "nav.settings",
};

const FOCUS_RING = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/70";
const ICON_BUTTON =
  "grid place-items-center rounded-sm border border-line-subtle bg-bg-2 text-fg-muted hover:text-fg hover:bg-bg-3 transition-colors " +
  FOCUS_RING;

type TopbarProps = { onMobileMenuToggle?: () => void };

// 2026-09-15 DALI v2.1: Der rechte Cluster (Zeitfenster, Vorwahl, Waehrung,
// Dichte, Sprache, Theme, Glocke, Identitaet) brach nie um. Gemessen: bei
// 320 px lief das Dokument auf 351 px, bei 768 px (Sidebar nimmt 232 px) auf
// 1047 px — die Seite scrollte horizontal. Unter lg wandern Waehrung, Dichte,
// Sprache und Theme in EIN "Mehr"-Menue mit 44-px-Zielen; sichtbar bleiben
// Vorwahl, Glocke, Mehr und die Identitaet. Nichts ist entfernt, nur gefaltet.
export function Topbar({ onMobileMenuToggle }: TopbarProps = {}) {
  const { theme, toggle } = useTheme();
  const { t, lang, setLang } = useT();
  const { currency, setCurrency } = useCurrency();
  const { timeframe, setTimeframe, density, setDensity } = useAppState();
  const { route } = useRouter();
  const [langOpen, setLangOpen] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);

  const currencySwitch = (
    <div
      className="inline-flex items-center rounded-sm border border-line-subtle bg-bg-2 p-0.5"
      role="group"
      aria-label={t("topbar.currency")}
      title={t("topbar.currency")}
    >
      {(["USD", "EUR"] as const).map((c) => (
        <button
          key={c}
          type="button"
          onClick={() => setCurrency(c as Currency)}
          className={cn(
            "min-h-[40px] lg:min-h-0 lg:h-7 px-2.5 rounded-xs text-2xs font-semibold font-mono",
            FOCUS_RING,
            currency === c ? "bg-bg-1 text-fg shadow-panel" : "text-fg-muted hover:text-fg",
          )}
          aria-pressed={currency === c}
        >
          {c === "EUR" ? "€ EUR" : "$ USD"}
        </button>
      ))}
    </div>
  );

  const densityButton = (
    <button
      type="button"
      onClick={() => setDensity(nextDensity(density))}
      className={cn("h-11 w-11 lg:h-8 lg:w-8", ICON_BUTTON)}
      aria-label={
        density === "compact"
          ? "Dichte: kompakt — zu komfortabel wechseln"
          : "Dichte: komfortabel — zu kompakt wechseln"
      }
      aria-pressed={density === "compact"}
      title={density === "compact" ? "Dichte: kompakt" : "Dichte: komfortabel"}
    >
      {density === "compact" ? <Rows2 size={15} /> : <Rows3 size={15} />}
    </button>
  );

  const themeButton = (
    <button
      type="button"
      onClick={toggle}
      className={cn("h-11 w-11 lg:h-8 lg:w-8", ICON_BUTTON)}
      aria-label={
        theme === "dark" ? "Zu hellem Erscheinungsbild wechseln" : "Zu dunklem Erscheinungsbild wechseln"
      }
      title={theme === "dark" ? t("topbar.theme_light") : t("topbar.theme_dark")}
    >
      {theme === "dark" ? <Sun size={14} /> : <Moon size={14} />}
    </button>
  );

  const langSwitch = (
    <div className="relative">
      <button
        type="button"
        onClick={() => setLangOpen((v) => !v)}
        className={cn(
          "h-11 lg:h-8 inline-flex items-center gap-1.5 rounded-sm border border-line-subtle bg-bg-2 px-2 text-xs hover:bg-bg-3 transition-colors",
          FOCUS_RING,
        )}
        aria-label="Sprache wählen"
        aria-expanded={langOpen}
        aria-haspopup="listbox"
      >
        <Languages size={13} />
        <span className="font-semibold font-mono uppercase">{lang}</span>
      </button>
      {langOpen && (
        <>
          <div className="fixed inset-0 z-30" onClick={() => setLangOpen(false)} />
          <div
            className={cn(
              "z-40 rounded-md border border-line bg-bg-1 shadow-raised p-1",
              "absolute top-full mt-1.5 right-0 w-[160px]",
            )}
            role="listbox"
          >
            {(["de", "en"] as const).map((l) => (
              <button
                key={l}
                type="button"
                role="option"
                aria-selected={lang === l}
                onClick={() => {
                  setLang(l);
                  setLangOpen(false);
                }}
                className={cn(
                  "w-full text-left flex items-center justify-between gap-2 px-2 min-h-[44px] lg:min-h-0 lg:py-1.5 rounded-sm text-xs",
                  FOCUS_RING,
                  lang === l ? "bg-bg-3 text-fg" : "text-fg-muted hover:bg-bg-2 hover:text-fg",
                )}
              >
                <span>{l === "de" ? t("topbar.lang_de") : t("topbar.lang_en")}</span>
                <span className="font-mono text-[10px] text-fg-subtle uppercase">{l}</span>
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );

  return (
    <header className="h-14 border-b border-line-subtle bg-bg-1/80 backdrop-blur flex items-center gap-1.5 sm:gap-2 md:gap-3 px-2 sm:px-3 md:px-5 sticky top-0 z-20">
      {onMobileMenuToggle && (
        <button
          type="button"
          onClick={onMobileMenuToggle}
          className={cn("md:hidden h-11 w-11 shrink-0", ICON_BUTTON)}
          aria-label="Alle Bereiche öffnen"
        >
          <Menu size={16} />
        </button>
      )}
      <div className="flex items-center gap-2 min-w-0">
        <span className="hidden sm:inline text-2xs font-semibold uppercase tracking-[0.1em] text-fg-subtle">
          {t("nav.section_operation")}
        </span>
        <span className="hidden sm:inline text-fg-subtle">/</span>
        <span className="text-sm font-semibold tracking-tight text-fg truncate">{t(CONTEXT[route])}</span>
      </div>

      <button
        type="button"
        onClick={() => window.dispatchEvent(new CustomEvent("kai:command-palette:open"))}
        className={cn(
          "relative hidden lg:flex items-center ml-4 flex-1 max-w-md h-8 pl-8 pr-16 rounded-sm border border-line-subtle bg-bg-2 text-xs text-fg-subtle hover:bg-bg-1 hover:border-line-strong transition-colors text-left",
          FOCUS_RING,
        )}
        aria-label={t("topbar.search")}
      >
        <Search size={14} className="absolute left-2.5 text-fg-subtle" />
        <span className="truncate">{t("topbar.search")}</span>
        <kbd className="absolute right-2 text-2xs font-mono text-fg-subtle border border-line-subtle bg-bg-1 rounded-xs px-1 py-0.5">
          ⌘K
        </kbd>
      </button>

      <div className="ml-auto flex items-center gap-1 sm:gap-2 shrink-0">
        <div
          className="hidden xl:flex items-center rounded-sm border border-line-subtle bg-bg-2 p-0.5"
          role="group"
          aria-label={t("topbar.timeframe")}
        >
          {TIMEFRAMES.map((tf) => (
            <button
              key={tf}
              type="button"
              onClick={() => setTimeframe(tf)}
              aria-pressed={timeframe === tf}
              className={cn(
                "px-2.5 h-6 rounded-xs text-2xs font-medium font-mono",
                FOCUS_RING,
                timeframe === tf ? "bg-bg-1 text-fg shadow-panel" : "text-fg-muted hover:text-fg",
              )}
            >
              {tf}
            </button>
          ))}
        </div>

        <ModeSelector />

        {/* Ab xl (1280 px) alles nebeneinander — bei 1024 px lief der Cluster
            neben der 232-px-Sidebar auf 1284 px (gemessen). */}
        <div className="hidden xl:flex items-center gap-2">
          {currencySwitch}
          {densityButton}
          {langSwitch}
          {themeButton}
        </div>

        <BackendStatusPill />

        <NotificationsBell />

        {/* … darunter gefaltet in EIN Menue. */}
        <div className="relative xl:hidden">
          <button
            type="button"
            onClick={() => setMoreOpen((v) => !v)}
            className={cn("h-11 w-11", ICON_BUTTON)}
            aria-label="Weitere Einstellungen: Währung, Dichte, Sprache, Erscheinungsbild"
            aria-expanded={moreOpen}
            aria-haspopup="dialog"
          >
            <MoreHorizontal size={16} />
          </button>
          {moreOpen && (
            <>
              <div className="fixed inset-0 z-30 bg-black/30" onClick={() => setMoreOpen(false)} />
              <div
                role="dialog"
                aria-label="Weitere Einstellungen"
                className="fixed z-40 top-[3.875rem] inset-x-2 rounded-md border border-line bg-bg-1 shadow-raised p-3 space-y-3"
              >
                <div className="flex items-center justify-between">
                  <span className="text-2xs font-semibold uppercase tracking-[0.08em] text-fg-subtle">
                    Weitere Einstellungen
                  </span>
                  <button
                    type="button"
                    onClick={() => setMoreOpen(false)}
                    className={cn("h-11 w-11", ICON_BUTTON)}
                    aria-label="Menü schließen"
                  >
                    <X size={16} />
                  </button>
                </div>
                <div className="flex items-center justify-between gap-3">
                  <span className="text-xs text-fg-muted">{t("topbar.currency")}</span>
                  {currencySwitch}
                </div>
                <div className="flex items-center justify-between gap-3">
                  <span className="text-xs text-fg-muted">
                    {density === "compact" ? "Dichte: kompakt" : "Dichte: komfortabel"}
                  </span>
                  {densityButton}
                </div>
                <div className="flex items-center justify-between gap-3">
                  <span className="text-xs text-fg-muted">Sprache</span>
                  {langSwitch}
                </div>
                <div className="flex items-center justify-between gap-3">
                  <span className="text-xs text-fg-muted">
                    {theme === "dark" ? t("topbar.theme_light") : t("topbar.theme_dark")}
                  </span>
                  {themeButton}
                </div>
              </div>
            </>
          )}
        </div>

        {/* Identitäts-Badge, KEIN Button: es gibt (noch) keine Profil-/Logout-
            Aktion im Backend, also keine klickbare Optik vortäuschen (war ein
            toter Button mit Hover-Affordance). Identität kommt via Cloudflare
            Access am Edge. */}
        <div
          className="flex items-center gap-2 h-8 pl-1 pr-1 xl:pr-2.5 rounded-sm border border-line-subtle bg-bg-2"
          title="Operator-Identität (via Cloudflare Access)"
        >
          <div className="h-6 w-6 rounded-xs bg-gradient-to-br from-accent to-ai grid place-items-center text-[10px] font-semibold text-white">
            SK
          </div>
          <span className="hidden xl:inline text-xs font-medium">Sascha</span>
        </div>
      </div>
    </header>
  );
}
