import { useEffect, useRef, useState } from "react";
import { Card, CardHeader, Badge } from "@/components/ui/Primitives";
import { useTheme } from "@/theme/ThemeProvider";
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

function cssVarToRgba(varName: string, alpha = 1): string {
  const raw = getComputedStyle(document.documentElement)
    .getPropertyValue(varName)
    .trim();
  const parts = raw.split(/\s+/).map(Number);
  if (parts.length === 3 && parts.every((n) => !isNaN(n))) {
    return `rgba(${parts[0]}, ${parts[1]}, ${parts[2]}, ${alpha})`;
  }
  return "";
}

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

    const bg =
      cssVarToRgba("--bg-1") ||
      (theme === "dark" ? "rgba(17, 21, 29, 1)" : "rgba(255, 255, 255, 1)");
    const grid =
      cssVarToRgba("--line-subtle", 0.06) ||
      (theme === "dark" ? "rgba(30, 36, 48, 0.06)" : "rgba(235, 237, 240, 0.06)");

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
    <Card padded className="overflow-hidden">
      <CardHeader
        title={title}
        subtitle={`${effectiveSymbol} · ${effectiveInterval}`}
        right={<StatusBadge status={status} mode={effectiveMode} />}
      />
      <div className={`relative w-full ${heightClass} rounded-sm bg-bg-1`}>
        {status.state === "disabled" && <DisabledOverlay />}
        {status.state === "unsupported" && (
          <UnsupportedOverlay message={status.message ?? ""} />
        )}
        {status.state === "error" && (
          <ErrorOverlay message={status.message ?? "Unbekannter Fehler"} />
        )}
        {enabled && effectiveMode === "widget" && (
          <div
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
    <div className="absolute inset-0 flex items-center justify-center p-6 text-center">
      <div className="max-w-md space-y-2 text-xs text-fg-muted">
        <p className="text-sm font-medium text-neg">
          Chart konnte nicht geladen werden
        </p>
        <p className="font-mono text-2xs">{message}</p>
      </div>
    </div>
  );
}
