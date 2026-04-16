import { useEffect, useMemo, useRef, useState } from "react";
import { Card, CardHeader, Badge } from "@/components/ui/Primitives";
import { useTheme } from "@/theme/ThemeProvider";
import { loadTradingViewEmbed } from "./widgetLoader";
import {
  DEFAULT_INTERVAL,
  DEFAULT_SYMBOL,
  type ChartMode,
  type TradingViewChartStatus,
  isEnabled,
  resolveMode,
} from "./types";

// Public KAI chart panel. In TV-1 this renders the official TradingView
// widget (https://s3.tradingview.com/tv.js). `advanced` and `trading_platform`
// modes are typed and wired but surface a transparent "prepared" placeholder
// since they require an external TradingView license application.
//
// Feature flags (all off by default):
//   VITE_TRADINGVIEW_ENABLED           "1" to activate
//   VITE_TRADINGVIEW_CHART_MODE        widget | advanced | trading_platform
//   VITE_TRADINGVIEW_DEFAULT_SYMBOL    e.g. BINANCE:BTCUSDT
//   VITE_TRADINGVIEW_DEFAULT_INTERVAL  e.g. 60

interface TradingViewChartProps {
  symbol?: string;
  interval?: string;
  mode?: ChartMode;
  heightClass?: string; // tailwind height utility, e.g. "h-[560px]"
  title?: string;
}

let _widgetCounter = 0;
function nextContainerId(): string {
  _widgetCounter += 1;
  return `kai-tv-widget-${_widgetCounter}`;
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

  const containerId = useMemo(() => nextContainerId(), []);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [status, setStatus] = useState<TradingViewChartStatus>({ state: "loading" });

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

    let cancelled = false;
    setStatus({ state: "loading" });

    loadTradingViewEmbed()
      .then(() => {
        if (cancelled) return;
        const TV = window.TradingView;
        if (!TV || !containerRef.current) {
          setStatus({ state: "error", message: "TradingView global not ready." });
          return;
        }
        containerRef.current.innerHTML = "";
        try {
          new TV.widget({
            container_id: containerId,
            symbol: effectiveSymbol,
            interval: effectiveInterval,
            theme: theme === "dark" ? "dark" : "light",
            autosize: true,
            timezone: "Etc/UTC",
            style: "1",
            locale: "en",
            hide_side_toolbar: false,
            allow_symbol_change: true,
            details: false,
            studies: [],
          });
          setStatus({ state: "ready" });
        } catch (err) {
          setStatus({
            state: "error",
            message: err instanceof Error ? err.message : "Widget init failed",
          });
        }
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setStatus({
          state: "error",
          message: err instanceof Error ? err.message : "Script load failed",
        });
      });

    return () => {
      cancelled = true;
    };
  }, [
    enabled,
    effectiveMode,
    effectiveSymbol,
    effectiveInterval,
    containerId,
    theme,
  ]);

  return (
    <Card padded className="overflow-hidden">
      <CardHeader
        title={title}
        subtitle={`${effectiveSymbol} · ${effectiveInterval}`}
        right={<StatusBadge status={status} mode={effectiveMode} />}
      />
      <div className={`relative w-full ${heightClass} rounded-sm bg-bg-2`}>
        {status.state === "disabled" && (
          <DisabledOverlay />
        )}
        {status.state === "unsupported" && (
          <UnsupportedOverlay message={status.message ?? ""} />
        )}
        {status.state === "error" && (
          <ErrorOverlay message={status.message ?? "Unbekannter Fehler"} />
        )}
        {enabled && effectiveMode === "widget" && (
          <div
            id={containerId}
            ref={containerRef}
            className="absolute inset-0"
            role="region"
            aria-label={`TradingView chart ${effectiveSymbol}`}
          />
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
  if (status.state === "disabled") return <Badge tone="muted" dot>Deaktiviert</Badge>;
  if (status.state === "unsupported") return <Badge tone="warn" dot>{mode}</Badge>;
  if (status.state === "loading") return <Badge tone="info" dot>Lädt …</Badge>;
  if (status.state === "error") return <Badge tone="neg" dot>Fehler</Badge>;
  return <Badge tone="pos" dot>Live</Badge>;
}

function DisabledOverlay() {
  return (
    <div className="absolute inset-0 flex items-center justify-center p-6 text-center">
      <div className="max-w-md space-y-2 text-xs text-fg-muted">
        <p className="text-sm font-medium text-fg">TradingView-Chart deaktiviert</p>
        <p>
          Aktivieren über <span className="font-mono">VITE_TRADINGVIEW_ENABLED=1</span> in
          <span className="font-mono"> web/.env</span>. In TV-1 nur Visualisierung; keine
          Signal-Pipeline-Anbindung.
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
          Details: <span className="font-mono">docs/adr/0001-tradingview-integration.md</span>
        </p>
      </div>
    </div>
  );
}

function ErrorOverlay({ message }: { message: string }) {
  return (
    <div className="absolute inset-0 flex items-center justify-center p-6 text-center">
      <div className="max-w-md space-y-2 text-xs text-fg-muted">
        <p className="text-sm font-medium text-neg">Chart konnte nicht geladen werden</p>
        <p className="font-mono text-2xs">{message}</p>
      </div>
    </div>
  );
}
