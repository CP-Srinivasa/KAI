import { useEffect, useRef, useState } from "react";
import { Maximize2, Minimize2 } from "lucide-react";
import { Card, CardHeader, Badge } from "@/components/ui/Primitives";
import { useTheme } from "@/theme/ThemeProvider";
import { cn } from "@/lib/utils";
import {
  DEFAULT_INTERVAL,
  DEFAULT_SYMBOL,
  type ChartMode,
  type TradingViewChartStatus,
  isEnabled,
  resolveMode,
} from "./types";

const EMBED_SCRIPT =
  "https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js";

// Operator-Folge 2026-05-08:
// Theme-Werte deterministisch aus dem theme-State, nicht aus
// getComputedStyle(documentElement). Beim Theme-Toggle laeuft das
// .dark-Class-Toggle (ThemeProvider.useEffect) parallel zu unserem
// useEffect — Reihenfolge ist nicht garantiert. Hardcoded-Mapping
// (Werte aus index.css gespiegelt) eliminiert das Race komplett.
// Quelle: web/src/index.css :root + .dark
const THEME_BG = {
  light: "rgba(255, 255, 255, 1)",
  dark: "rgba(17, 21, 29, 1)",
} as const;
const THEME_GRID = {
  light: "rgba(235, 237, 240, 0.5)",
  dark: "rgba(30, 36, 48, 0.5)",
} as const;

interface TradingViewChartProps {
  symbol?: string;
  interval?: string;
  mode?: ChartMode;
  heightClass?: string;
  title?: string;
}

export function TradingViewChart({
  symbol,
  interval,
  mode,
  heightClass = "h-[520px]",
  title = "Chart",
}: TradingViewChartProps) {
  const { theme } = useTheme();
  const effectiveMode: ChartMode = mode ?? resolveMode();
  const effectiveSymbol =
    symbol ?? import.meta.env.VITE_TRADINGVIEW_DEFAULT_SYMBOL ?? DEFAULT_SYMBOL;
  const effectiveInterval =
    interval ?? import.meta.env.VITE_TRADINGVIEW_DEFAULT_INTERVAL ?? DEFAULT_INTERVAL;
  const enabled = isEnabled();

  const containerRef = useRef<HTMLDivElement | null>(null);
  const [status, setStatus] = useState<TradingViewChartStatus>({ state: "loading" });
  // WP-4: Vollbild-Chart (§21). Escape verlässt Vollbild.
  const [fullscreen, setFullscreen] = useState(false);
  useEffect(() => {
    if (!fullscreen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setFullscreen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [fullscreen]);

  useEffect(() => {
    if (!enabled) {
      setStatus({ state: "disabled" });
      return;
    }
    if (effectiveMode !== "widget") {
      setStatus({
        state: "unsupported",
        message:
          effectiveMode === "advanced"
            ? "Advanced Charts Library benötigt separaten Lizenzantrag bei TradingView."
            : "Trading Platform benötigt Lizenz + Datenfeed-Provider.",
      });
      return;
    }

    const el = containerRef.current;
    if (!el) return;

    el.innerHTML = "";
    setStatus({ state: "loading" });

    const bg = THEME_BG[theme];
    const grid = THEME_GRID[theme];

    const config = {
      autosize: true,
      symbol: effectiveSymbol,
      interval: effectiveInterval,
      timezone: "Etc/UTC",
      theme: theme === "dark" ? "dark" : "light",
      style: "1",
      locale: "en",
      backgroundColor: bg,
      gridColor: grid,
      hide_side_toolbar: false,
      allow_symbol_change: true,
      calendar: false,
      support_host: "https://www.tradingview.com",
    };

    const wrapper = document.createElement("div");
    wrapper.className = "tradingview-widget-container";
    wrapper.style.height = "100%";
    wrapper.style.width = "100%";

    const widgetDiv = document.createElement("div");
    widgetDiv.className = "tradingview-widget-container__widget";
    widgetDiv.style.height = "calc(100% - 32px)";
    widgetDiv.style.width = "100%";
    wrapper.appendChild(widgetDiv);

    const script = document.createElement("script");
    script.type = "text/javascript";
    script.src = EMBED_SCRIPT;
    script.async = true;
    script.textContent = JSON.stringify(config);
    script.addEventListener("load", () => setStatus({ state: "ready" }), {
      once: true,
    });
    script.addEventListener(
      "error",
      () =>
        setStatus({ state: "error", message: "Embed script load failed" }),
      { once: true },
    );
    wrapper.appendChild(script);
    el.appendChild(wrapper);

    return () => {
      el.innerHTML = "";
    };
  }, [enabled, effectiveMode, effectiveSymbol, effectiveInterval, theme]);

  return (
    <Card
      padded
      className={cn("overflow-hidden", fullscreen && "fixed inset-0 z-50 m-0 flex flex-col rounded-none")}
    >
      <CardHeader
        title={title}
        subtitle={`${effectiveSymbol} · ${effectiveInterval}`}
        right={
          <div className="flex items-center gap-2">
            <StatusBadge status={status} mode={effectiveMode} />
            <button
              type="button"
              onClick={() => setFullscreen((v) => !v)}
              className="h-7 w-7 grid place-items-center rounded-sm border border-line-subtle bg-bg-2 text-fg-muted hover:text-fg hover:bg-bg-3 transition-colors"
              aria-label={fullscreen ? "Vollbild verlassen (Esc)" : "Chart im Vollbild"}
              title={fullscreen ? "Vollbild verlassen (Esc)" : "Vollbild"}
            >
              {fullscreen ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
            </button>
          </div>
        }
      />
      <div className={cn("relative w-full rounded-md bg-bg-1 border border-line-subtle overflow-hidden", fullscreen ? "flex-1 min-h-0" : heightClass)}>
        {status.state === "disabled" && <DisabledOverlay />}
        {status.state === "unsupported" && (
          <UnsupportedOverlay message={status.message ?? ""} />
        )}
        {status.state === "error" && (
          <ErrorOverlay message={status.message ?? "Unbekannter Fehler"} />
        )}
        {enabled && effectiveMode === "widget" && (
          <>
            <div
              ref={containerRef}
              className="absolute inset-0"
              role="region"
              aria-label={`TradingView chart ${effectiveSymbol}`}
            />
            {status.state === "loading" && (
              <div
                className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-bg-1 pointer-events-none"
                aria-hidden="true"
              >
                <div className="flex items-center gap-1.5">
                  <span className="h-2 w-2 rounded-full bg-fg-subtle/50 animate-pulse" />
                  <span className="h-2 w-2 rounded-full bg-fg-subtle/50 animate-pulse" style={{ animationDelay: "150ms" }} />
                  <span className="h-2 w-2 rounded-full bg-fg-subtle/50 animate-pulse" style={{ animationDelay: "300ms" }} />
                </div>
                <span className="text-2xs text-fg-subtle font-mono uppercase tracking-wide">
                  Lade Markt-Snapshot …
                </span>
              </div>
            )}
          </>
        )}
      </div>
    </Card>
  );
}

function StatusBadge({
  status,
  mode,
}: {
  status: TradingViewChartStatus;
  mode: ChartMode;
}) {
  if (status.state === "disabled")
    return (
      <Badge tone="muted" dot>
        Deaktiviert
      </Badge>
    );
  if (status.state === "unsupported")
    return (
      <Badge tone="warn" dot>
        {mode}
      </Badge>
    );
  if (status.state === "loading")
    return (
      <Badge tone="info" dot>
        Lädt …
      </Badge>
    );
  if (status.state === "error")
    return (
      <Badge tone="neg" dot>
        Fehler
      </Badge>
    );
  return (
    <Badge tone="pos" dot>
      Live
    </Badge>
  );
}

function DisabledOverlay() {
  return (
    <div className="absolute inset-0 flex items-center justify-center p-6 text-center">
      <div className="max-w-md space-y-2 text-xs text-fg-muted">
        <p className="text-sm font-medium text-fg">TradingView-Chart deaktiviert</p>
        <p>
          Aktivieren über{" "}
          <span className="font-mono">VITE_TRADINGVIEW_ENABLED=1</span> in
          <span className="font-mono"> web/.env</span>. In TV-1 nur
          Visualisierung; keine Signal-Pipeline-Anbindung.
        </p>
      </div>
    </div>
  );
}

function UnsupportedOverlay({ message }: { message: string }) {
  return (
    <div className="absolute inset-0 flex items-center justify-center p-6 text-center">
      <div className="max-w-md space-y-2 text-xs text-fg-muted">
        <p className="text-sm font-medium text-fg">Modus nicht aktiviert</p>
        <p>{message}</p>
        <p className="text-2xs text-fg-subtle">
          Details:{" "}
          <span className="font-mono">
            docs/adr/0001-tradingview-integration.md
          </span>
        </p>
      </div>
    </div>
  );
}

function ErrorOverlay({ message }: { message: string }) {
  return (
    <div className="absolute inset-0 flex items-center justify-center p-6 text-center bg-neg/5 attention-breathe-neg">
      <div className="max-w-md space-y-2 text-xs text-fg-muted">
        <p className="text-sm font-medium text-neg">
          Chart konnte nicht geladen werden
        </p>
        <p className="font-mono text-2xs">{message}</p>
      </div>
    </div>
  );
}
