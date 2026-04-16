import { Bell, Moon, Search, Sun, Languages, Menu } from "lucide-react";
import { useState } from "react";
import { useTheme } from "@/theme/ThemeProvider";
import { Badge } from "@/components/ui/Primitives";
import { ModeSelector } from "@/components/trading/ModeSelector";
import { useT } from "@/i18n/I18nProvider";
import { useCurrency, type Currency } from "@/state/CurrencyProvider";
import { useAppState, TIMEFRAMES } from "@/state/AppState";
import { useRouter, type Route } from "@/state/Router";
import { cn } from "@/lib/utils";

const CONTEXT: Record<Route, string> = {
  dashboard: "nav.dashboard",
  signals: "nav.signals",
  markets: "nav.markets",
  trades: "nav.trades",
  portfolio: "nav.portfolio",
  risk: "nav.risk",
  ai: "nav.ai",
  alerts: "nav.alerts",
  news: "nav.news",
  backtest: "nav.backtest",
  external: "nav.external",
  agents: "nav.agents",
  settings: "nav.settings",
};

type TopbarProps = { onMobileMenuToggle?: () => void };

export function Topbar({ onMobileMenuToggle }: TopbarProps = {}) {
  const { theme, toggle } = useTheme();
  const { t, lang, setLang } = useT();
  const { currency, setCurrency } = useCurrency();
  const { timeframe, setTimeframe } = useAppState();
  const { route } = useRouter();
  const [langOpen, setLangOpen] = useState(false);

  return (
    <header className="h-14 border-b border-line-subtle bg-bg-1/80 backdrop-blur flex items-center gap-2 sm:gap-3 px-3 sm:px-5 sticky top-0 z-20">
      {onMobileMenuToggle && (
        <button
          onClick={onMobileMenuToggle}
          className="md:hidden h-8 w-8 grid place-items-center rounded-sm border border-line-subtle bg-bg-2 text-fg-muted hover:text-fg hover:bg-bg-3 transition-colors shrink-0"
          aria-label="Open navigation menu"
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

      <div className="relative hidden md:flex items-center ml-4 flex-1 max-w-md">
        <Search size={14} className="absolute left-2.5 text-fg-subtle" />
        <input
          type="text"
          placeholder={t("topbar.search")}
          className="w-full h-8 pl-8 pr-16 rounded-sm border border-line-subtle bg-bg-2 text-xs placeholder:text-fg-subtle focus:outline-none focus:border-line-strong focus:bg-bg-1 transition-colors"
        />
        <kbd className="absolute right-2 text-[10px] font-mono text-fg-subtle border border-line-subtle bg-bg-1 rounded-xs px-1 py-0.5">
          ⌘K
        </kbd>
      </div>

      <div className="ml-auto flex items-center gap-2">
        <div className="hidden lg:flex items-center rounded-sm border border-line-subtle bg-bg-2 p-0.5" role="group" aria-label={t("topbar.timeframe")}>
          {TIMEFRAMES.map((tf) => (
            <button
              key={tf}
              onClick={() => setTimeframe(tf)}
              aria-pressed={timeframe === tf}
              className={cn(
                "px-2.5 h-6 rounded-xs text-2xs font-medium font-mono",
                timeframe === tf ? "bg-bg-1 text-fg shadow-panel" : "text-fg-muted hover:text-fg",
              )}
            >
              {tf}
            </button>
          ))}
        </div>

        <ModeSelector />

        <div className="inline-flex items-center rounded-sm border border-line-subtle bg-bg-2 p-0.5" title={t("topbar.currency")}>
          {(["USD", "EUR"] as const).map((c) => (
            <button
              key={c}
              onClick={() => setCurrency(c as Currency)}
              className={cn(
                "h-7 px-2.5 rounded-xs text-2xs font-semibold font-mono",
                currency === c ? "bg-bg-1 text-fg shadow-panel" : "text-fg-muted hover:text-fg",
              )}
              aria-pressed={currency === c}
            >
              {c === "EUR" ? "€ EUR" : "$ USD"}
            </button>
          ))}
        </div>

        <div className="relative">
          <button
            onClick={() => setLangOpen((v) => !v)}
            className="h-8 inline-flex items-center gap-1.5 rounded-sm border border-line-subtle bg-bg-2 px-2 text-xs hover:bg-bg-3 transition-colors"
            aria-label="Language"
          >
            <Languages size={13} />
            <span className="font-semibold font-mono uppercase">{lang}</span>
          </button>
          {langOpen && (
            <>
              <div className="fixed inset-0 z-30" onClick={() => setLangOpen(false)} />
              <div className="absolute right-0 top-full mt-1.5 z-40 w-[160px] rounded-md border border-line bg-bg-1 shadow-raised p-1">
                {(["de", "en"] as const).map((l) => (
                  <button
                    key={l}
                    onClick={() => {
                      setLang(l);
                      setLangOpen(false);
                    }}
                    className={cn(
                      "w-full text-left flex items-center justify-between gap-2 px-2 py-1.5 rounded-sm text-xs",
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

        <button
          onClick={toggle}
          className="h-8 w-8 grid place-items-center rounded-sm border border-line-subtle bg-bg-2 text-fg-muted hover:text-fg hover:bg-bg-3 transition-colors"
          aria-label="Toggle theme"
          title={theme === "dark" ? t("topbar.theme_light") : t("topbar.theme_dark")}
        >
          {theme === "dark" ? <Sun size={14} /> : <Moon size={14} />}
        </button>

        <button
          className="relative h-8 w-8 grid place-items-center rounded-sm border border-line-subtle bg-bg-2 text-fg-muted hover:text-fg hover:bg-bg-3 transition-colors"
          aria-label="Notifications"
        >
          <Bell size={14} />
          <span className="absolute top-1.5 right-1.5 h-1.5 w-1.5 rounded-full bg-neg ring-2 ring-bg-2" />
        </button>

        <div className="hidden xl:flex items-center gap-2 rounded-sm border border-line-subtle bg-bg-2 px-2.5 h-8">
          <Badge tone="warn" dot>
            {t("topbar.env_split")}
          </Badge>
          <span className="text-2xs text-fg-subtle font-mono">D-137</span>
        </div>

        <button className="flex items-center gap-2 h-8 pl-1 pr-2.5 rounded-sm border border-line-subtle bg-bg-2 hover:bg-bg-3 transition-colors">
          <div className="h-6 w-6 rounded-xs bg-gradient-to-br from-accent to-ai grid place-items-center text-[10px] font-semibold text-white">
            SK
          </div>
          <span className="text-xs font-medium">Sascha</span>
        </button>
      </div>
    </header>
  );
}
