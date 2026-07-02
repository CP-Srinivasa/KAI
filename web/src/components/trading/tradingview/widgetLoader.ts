// Loads the official TradingView widget bundle once per page.
//
// Script URL: https://s3.tradingview.com/tv.js (TradingView's public embed).
// The script is loaded lazily on first demand; subsequent callers reuse the
// in-flight promise. No credentials, no cookies, no privileged access required.

const SCRIPT_URL = "https://s3.tradingview.com/tv.js";
const SCRIPT_ID = "kai-tradingview-embed";

declare global {
  interface Window {
    TradingView?: {
      widget: new (options: Record<string, unknown>) => unknown;
    };
  }
}

let _loadingPromise: Promise<void> | null = null;

export function loadTradingViewEmbed(): Promise<void> {
  if (typeof window === "undefined") {
    return Promise.reject(new Error("TradingView embed requires a browser"));
  }
  if (window.TradingView) return Promise.resolve();
  if (_loadingPromise) return _loadingPromise;

  _loadingPromise = new Promise<void>((resolve, reject) => {
    const existing = document.getElementById(SCRIPT_ID) as HTMLScriptElement | null;
    const script = existing ?? document.createElement("script");
    if (!existing) {
      script.id = SCRIPT_ID;
      script.src = SCRIPT_URL;
      script.async = true;
      document.head.appendChild(script);
    }
    script.addEventListener(
      "load",
      () => {
        if (window.TradingView) resolve();
        else reject(new Error("TradingView global not available after script load"));
      },
      { once: true },
    );
    script.addEventListener(
      "error",
      () => reject(new Error("Failed to load TradingView embed")),
      { once: true },
    );
    if (existing && window.TradingView) resolve();
  });
  return _loadingPromise;
}

// Test hook — resets the memoized promise so unit/integration tests can retry
// the load path without state leakage between specs.
export function __resetWidgetLoaderForTests(): void {
  _loadingPromise = null;
}
