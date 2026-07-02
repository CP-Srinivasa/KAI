// Provider abstraction for TradingView chart integration.
//
// TV-1 implements only `widget` — the official, no-license embed script at
// https://s3.tradingview.com/tv.js. `advanced` and `trading_platform` require
// a separate license application with TradingView and are rendered as
// transparent "prepared" placeholders until those artifacts are available.
//
// See: docs/adr/0001-tradingview-integration.md

export type ChartMode = "widget" | "advanced" | "trading_platform";

export type ChartTheme = "dark" | "light";

export interface TradingViewChartConfig {
  mode: ChartMode;
  symbol: string;
  interval: string; // e.g. "60", "240", "D"
  theme: ChartTheme;
  autosize?: boolean;
  studies?: readonly string[];
}

export interface TradingViewChartStatus {
  state: "disabled" | "loading" | "ready" | "error" | "unsupported";
  message?: string;
}

export const DEFAULT_SYMBOL = "BINANCE:BTCUSDT";
export const DEFAULT_INTERVAL = "60";

export function resolveMode(): ChartMode {
  const envMode = import.meta.env.VITE_TRADINGVIEW_CHART_MODE;
  if (envMode === "advanced" || envMode === "trading_platform") return envMode;
  return "widget";
}

export function isEnabled(): boolean {
  // TV-1 ist reine Visualisierung (kein Signal-Pipeline-Pfad) und standardmäßig
  // AN. Bewusst opt-OUT statt opt-in: der Chart wird über `vite build` ohne
  // gesetztes Flag sonst bei jedem Deploy stumm abgeschaltet (web/.env ist
  // gitignored und im Pi-Build nicht vorhanden — dreimal so verschwunden).
  // CSP erlaubt die TV-Origins per Default (security_headers_allow_tradingview).
  // Abschalten nur explizit über VITE_TRADINGVIEW_ENABLED=0 (oder "false").
  const v = import.meta.env.VITE_TRADINGVIEW_ENABLED;
  return v !== "0" && v?.toLowerCase() !== "false";
}
