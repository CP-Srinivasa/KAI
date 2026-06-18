/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_KAI_API_BASE?: string;
  // TradingView integration (TV-1, ON by default). Opt-out via "0"/"false".
  readonly VITE_TRADINGVIEW_ENABLED?: string;
  readonly VITE_TRADINGVIEW_CHART_MODE?: "widget" | "advanced" | "trading_platform";
  readonly VITE_TRADINGVIEW_DEFAULT_SYMBOL?: string;
  readonly VITE_TRADINGVIEW_DEFAULT_INTERVAL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
