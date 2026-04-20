import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

export type Currency = "USD" | "EUR";
export type FxSnapshot = { USD: 1; EUR: number };

// Static fallback — used as initial value before the live fetch resolves and
// whenever /dashboard/api/fx is unreachable. Mirrors the backend constant.
const FALLBACK_FX: FxSnapshot = { USD: 1, EUR: 0.921 };

// Mutable module-level snapshot so non-component code paths (charts, formatters
// instantiated outside the React tree) can read the most recent rate. Updated
// by the provider whenever the live fetch succeeds.
export let currentFx: FxSnapshot = FALLBACK_FX;

type FxMeta = {
  source: string;
  asOf: string;
  fetchedAt: string;
  live: boolean;
};

type Ctx = {
  currency: Currency;
  setCurrency: (c: Currency) => void;
  /** Format a USD-denominated amount in the active display currency.
   *  If `fx` is provided it is used as the historical rate at capture time;
   *  otherwise the live (current) rate is used. */
  fmt: (usdAmount: number, fx?: FxSnapshot, digits?: number) => string;
  /** Raw conversion without formatting — for chart tooltips etc. */
  convert: (usdAmount: number, fx?: FxSnapshot) => number;
  /** The active currency's symbol. */
  symbol: string;
  /** Current FX snapshot (live or fallback) — re-renders when refreshed. */
  fx: FxSnapshot;
  /** Provenance of the active rate. `null` until the first fetch resolves. */
  fxMeta: FxMeta | null;
};

const Context = createContext<Ctx | null>(null);
const KEY = "kai-currency";
const FX_REFRESH_MS = 60 * 60 * 1000; // 1h — backend caches 1h, ECB updates daily

function initial(): Currency {
  try {
    const v = localStorage.getItem(KEY);
    if (v === "USD" || v === "EUR") return v;
  } catch {}
  if (typeof navigator !== "undefined" && navigator.language?.toLowerCase().startsWith("de")) return "EUR";
  return "USD";
}

type FxApiResponse = {
  base: string;
  rates: { USD: number; EUR: number };
  source: string;
  as_of: string;
  fetched_at: string;
  live: boolean;
};

export function CurrencyProvider({ children }: { children: ReactNode }) {
  const [currency, _set] = useState<Currency>(initial);
  const [fx, setFx] = useState<FxSnapshot>(FALLBACK_FX);
  const [fxMeta, setFxMeta] = useState<FxMeta | null>(null);

  useEffect(() => {
    try {
      localStorage.setItem(KEY, currency);
    } catch {}
  }, [currency]);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const r = await fetch("/dashboard/api/fx", { credentials: "same-origin" });
        if (!r.ok) return;
        const data = (await r.json()) as FxApiResponse;
        const eur = Number(data?.rates?.EUR);
        if (!Number.isFinite(eur) || eur <= 0) return;
        const snap: FxSnapshot = { USD: 1, EUR: eur };
        if (cancelled) return;
        currentFx = snap;
        setFx(snap);
        setFxMeta({
          source: data.source,
          asOf: data.as_of,
          fetchedAt: data.fetched_at,
          live: !!data.live,
        });
      } catch {
        // network/parse failure — keep static fallback, no UI noise
      }
    };
    load();
    const id = window.setInterval(load, FX_REFRESH_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, []);

  const setCurrency = useCallback((c: Currency) => _set(c), []);

  const convert = useCallback(
    (usd: number, snap?: FxSnapshot) => {
      const rate = (snap ?? fx)[currency];
      return usd * rate;
    },
    [currency, fx],
  );

  const fmt = useCallback(
    (usd: number, snap?: FxSnapshot, digits = 2) => {
      const value = convert(usd, snap);
      const locale = currency === "EUR" ? "de-DE" : "en-US";
      const abs = Math.abs(value).toLocaleString(locale, {
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
      });
      const sym = currency === "EUR" ? "€" : "$";
      const sign = value < 0 ? "-" : "";
      return currency === "EUR" ? `${sign}${abs} ${sym}` : `${sign}${sym}${abs}`;
    },
    [convert, currency],
  );

  const symbol = currency === "EUR" ? "€" : "$";

  const value = useMemo(
    () => ({ currency, setCurrency, fmt, convert, symbol, fx, fxMeta }),
    [currency, setCurrency, fmt, convert, symbol, fx, fxMeta],
  );

  return <Context.Provider value={value}>{children}</Context.Provider>;
}

export function useCurrency() {
  const c = useContext(Context);
  if (!c) throw new Error("useCurrency outside CurrencyProvider");
  return c;
}
