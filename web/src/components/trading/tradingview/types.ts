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
  return import.meta.env.VITE_TRADINGVIEW_ENABLED === "1";
}
