import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  ArrowRight,
  Bell,
  Bot,
  Briefcase,
  Globe,
  LayoutDashboard,
  LineChart,
  Languages as LanguagesIcon,
  Moon,
  Newspaper,
  Radio,
  Rewind,
  Search,
  Settings,
  ShieldAlert,
  Sparkles,
  Sun,
  Upload,
  ArrowLeftRight,
  Wallet,
  Zap,
  Bitcoin,
  Database,
} from "lucide-react";
import { useRouter, ROUTES, type Route } from "@/state/Router";
import { useTheme } from "@/theme/ThemeProvider";
import { useT } from "@/i18n/I18nProvider";
import { useCurrency } from "@/state/CurrencyProvider";
import { useAppState } from "@/state/AppState";
import { fetchRecentEnvelopes, type EnvelopeRecord } from "@/lib/api";
import { cn } from "@/lib/utils";

type Section = "pages" | "actions" | "signals";

type CommandItem = {
  id: string;
  section: Section;
  label: string;
  hint?: string;
  icon: ReactNode;
  keywords: string[];
  run: () => void;
};

const ROUTE_ICONS: Record<Route, ReactNode> = {
  dashboard: <LayoutDashboard size={14} />,
  signals: <Radio size={14} />,
  markets: <LineChart size={14} />,
  trades: <ArrowLeftRight size={14} />,
  portfolio: <Briefcase size={14} />,
  risk: <ShieldAlert size={14} />,
  ai: <Sparkles size={14} />,
  alerts: <Bell size={14} />,
  news: <Newspaper size={14} />,
  backtest: <Rewind size={14} />,
  external: <Upload size={14} />,
  sources: <Database size={14} />,
  node: <Bitcoin size={14} />,
  agents: <Bot size={14} />,
  settings: <Settings size={14} />,
};

const SECTION_LABEL: Record<Section, string> = {
  pages: "Seiten",
  actions: "Aktionen",
  signals: "Letzte Signale",
};

export function CommandPalette() {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState(0);
  const [signals, setSignals] = useState<EnvelopeRecord[]>([]);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);

  const { navigate } = useRouter();
  const { theme, toggle: toggleTheme } = useTheme();
  const { t, lang, setLang } = useT();
  const { currency, setCurrency } = useCurrency();
  const { mode, setMode } = useAppState();

  const close = useCallback(() => {
    setOpen(false);
    setQuery("");
    setSelected(0);
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const isCmdK = (e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k";
      if (isCmdK) {
        e.preventDefault();
        setOpen((v) => !v);
      }
    };
    const onOpen = () => setOpen(true);
    window.addEventListener("keydown", onKey);
    window.addEventListener("kai:command-palette:open", onOpen as EventListener);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("kai:command-palette:open", onOpen as EventListener);
    };
  }, []);

  useEffect(() => {
    if (!open) return;
    inputRef.current?.focus();
    const ctrl = new AbortController();
    fetchRecentEnvelopes(12, ctrl.signal)
      .then((r) => {
        const onlySignals = r.records.filter((x) => x.message_type === "signal" && x.signal);
        setSignals(onlySignals.slice(0, 8));
      })
      .catch(() => setSignals([]));
    return () => ctrl.abort();
  }, [open]);

  const runAndClose = useCallback(
    (fn: () => void) => {
      fn();
      close();
    },
    [close],
  );

  const items = useMemo<CommandItem[]>(() => {
    const pages: CommandItem[] = ROUTES.map((r) => ({
      id: `page-${r}`,
      section: "pages",
      label: t(`nav.${r}`),
      icon: ROUTE_ICONS[r],
      keywords: [r, t(`nav.${r}`).toLowerCase()],
      run: () => runAndClose(() => navigate(r)),
    }));

    const actions: CommandItem[] = [
      {
        id: "act-theme",
        section: "actions",
        label: theme === "dark" ? t("topbar.theme_light") : t("topbar.theme_dark"),
        hint: theme === "dark" ? "aktiv: dark" : "aktiv: light",
        icon: theme === "dark" ? <Sun size={14} /> : <Moon size={14} />,
        keywords: ["theme", "dark", "light", "mode"],
        run: () => runAndClose(toggleTheme),
      },
      {
        id: "act-lang",
        section: "actions",
        label: lang === "de" ? "Sprache → English" : "Language → Deutsch",
        hint: `aktiv: ${lang.toUpperCase()}`,
        icon: <LanguagesIcon size={14} />,
        keywords: ["language", "sprache", "de", "en"],
        run: () => runAndClose(() => setLang(lang === "de" ? "en" : "de")),
      },
      {
        id: "act-currency",
        section: "actions",
        label: `Währung → ${currency === "USD" ? "EUR" : "USD"}`,
        hint: `aktiv: ${currency}`,
        icon: <Wallet size={14} />,
        keywords: ["currency", "währung", "usd", "eur"],
        run: () => runAndClose(() => setCurrency(currency === "USD" ? "EUR" : "USD")),
      },
      {
        id: "act-mode-paper",
        section: "actions",
        label: "Trading-Modus → Paper",
        hint: mode === "paper" ? "aktiv" : undefined,
        icon: <Zap size={14} />,
        keywords: ["mode", "modus", "paper", "trading"],
        run: () => runAndClose(() => setMode("paper")),
      },
      {
        id: "act-paste",
        section: "actions",
        label: "Externes Signal einfügen",
        icon: <Upload size={14} />,
        keywords: ["signal", "paste", "external", "externe"],
        run: () => runAndClose(() => navigate("external")),
      },
      {
        id: "act-agents",
        section: "actions",
        label: "Agenten-Konsole öffnen",
        icon: <Bot size={14} />,
        keywords: ["agents", "agenten", "dali", "sentr", "neo", "satoshi"],
        run: () => runAndClose(() => navigate("agents")),
      },
    ];

    const sigItems: CommandItem[] = signals.map((env, i) => {
      const s = env.signal!;
      const sym = s.symbol ?? "?";
      const dir = (s.direction ?? "").toUpperCase();
      return {
        id: `sig-${env.envelope_id ?? i}`,
        section: "signals",
        label: `${sym} ${dir}`.trim(),
        hint: [s.entry_value != null ? `@ ${s.entry_value}` : null, s.signal_status].filter(Boolean).join(" · "),
        icon: <Radio size={14} />,
        keywords: [sym.toLowerCase(), dir.toLowerCase(), s.signal_status?.toLowerCase() ?? ""],
        run: () => runAndClose(() => navigate("external")),
      };
    });

    return [...pages, ...actions, ...sigItems];
  }, [navigate, runAndClose, theme, toggleTheme, t, lang, setLang, currency, setCurrency, mode, setMode, signals]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return items;
    return items.filter(
      (it) =>
        it.label.toLowerCase().includes(q) ||
        it.hint?.toLowerCase().includes(q) ||
        it.keywords.some((k) => k.includes(q)),
    );
  }, [items, query]);

  useEffect(() => {
    setSelected(0);
  }, [query, open]);

  useEffect(() => {
    if (!open) return;
    const el = listRef.current?.querySelector<HTMLButtonElement>(`[data-idx="${selected}"]`);
    el?.scrollIntoView({ block: "nearest" });
  }, [selected, open]);

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") {
      close();
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setSelected((s) => Math.min(filtered.length - 1, s + 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setSelected((s) => Math.max(0, s - 1));
    } else if (e.key === "Enter") {
      e.preventDefault();
      filtered[selected]?.run();
    }
  };

  if (!open) return null;

  const grouped: Array<{ section: Section; items: CommandItem[] }> = [];
  (["pages", "actions", "signals"] as Section[]).forEach((sec) => {
    const list = filtered.filter((i) => i.section === sec);
    if (list.length) grouped.push({ section: sec, items: list });
  });

  let runningIdx = 0;

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center pt-[12vh] px-4 bg-black/40 backdrop-blur-sm"
      onClick={close}
      role="dialog"
      aria-modal
      aria-label="Command Palette"
    >
      <div
        className="w-full max-w-xl rounded-lg border border-line bg-bg-1 shadow-raised overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-2 px-3 h-11 border-b border-line-subtle">
          <Search size={14} className="text-fg-subtle shrink-0" />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Suche Seiten, Aktionen, Signale…"
            className="flex-1 bg-transparent text-sm text-fg placeholder:text-fg-subtle focus:outline-none"
            aria-label="Command Palette Suche"
          />
          <kbd className="text-[10px] font-mono text-fg-subtle border border-line-subtle bg-bg-2 rounded-xs px-1 py-0.5 shrink-0">
            ESC
          </kbd>
        </div>

        <div ref={listRef} className="max-h-[50vh] overflow-y-auto py-1">
          {grouped.length === 0 && (
            <div className="px-3 py-6 text-center text-xs text-fg-subtle">
              Keine Treffer für „{query}"
            </div>
          )}
          {grouped.map((g) => (
            <div key={g.section}>
              <div className="px-3 pt-2 pb-1 text-2xs font-semibold uppercase tracking-[0.08em] text-fg-subtle">
                {SECTION_LABEL[g.section]}
              </div>
              {g.items.map((it) => {
                const idx = runningIdx++;
                const active = idx === selected;
                return (
                  <button
                    key={it.id}
                    data-idx={idx}
                    onClick={it.run}
                    onMouseEnter={() => setSelected(idx)}
                    className={cn(
                      "w-full flex items-center gap-3 px-3 py-2 text-left text-xs transition-colors",
                      active ? "bg-bg-3 text-fg" : "text-fg-muted hover:bg-bg-2",
                    )}
                  >
                    <span className={cn("shrink-0", active ? "text-accent" : "text-fg-subtle")}>
                      {it.icon}
                    </span>
                    <span className="flex-1 truncate font-medium">{it.label}</span>
                    {it.hint && (
                      <span className="text-2xs text-fg-subtle font-mono shrink-0">{it.hint}</span>
                    )}
                    {active && <ArrowRight size={12} className="text-accent shrink-0" />}
                  </button>
                );
              })}
            </div>
          ))}
        </div>

        <div className="flex items-center justify-between px-3 h-8 border-t border-line-subtle text-2xs text-fg-subtle font-mono">
          <span className="inline-flex items-center gap-1">
            <Globe size={10} aria-hidden />
            {filtered.length} von {items.length}
          </span>
          <span className="inline-flex items-center gap-2">
            <kbd className="border border-line-subtle bg-bg-2 rounded-xs px-1">↑↓</kbd>
            <span>Nav</span>
            <kbd className="border border-line-subtle bg-bg-2 rounded-xs px-1">↵</kbd>
            <span>Öffnen</span>
          </span>
        </div>
      </div>
    </div>
  );
}
