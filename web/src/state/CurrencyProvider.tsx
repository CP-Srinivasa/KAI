import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import {
  convertUsd,
  formatMoney,
  formatNumber,
  formatPct,
  formatPrice,
  type Currency,
  type FxSnapshot,
} from "@/lib/money";

// Re-exported for backward-compat: consumers import these from here.
export type { Currency, FxSnapshot } from "@/lib/money";

type MoneyArg = number | string | null | undefined;

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
  /** Format a USD-denominated capital amount (PnL/equity/cash/value) in the
   *  active display currency. If `fx` is provided it is used as the historical
   *  rate at capture time; otherwise the live (current) rate is used. */
  fmt: (usdAmount: MoneyArg, fx?: FxSnapshot, digits?: number) => string;
  /** Format a quoted instrument price (entry/stop/target/market) with adaptive
   *  decimals (sub-cent micro-caps stay readable), currency-converted. */
  fmtPrice: (usdPrice: MoneyArg, fx?: FxSnapshot) => string;
  /** Format a percentage in the active number locale (never currency-converted).
   *  `signed` prefixes "+" on positives. */
  fmtPct: (value: MoneyArg, opts?: { digits?: number; signed?: boolean }) => string;
  /** Format a plain number (count / n-value / quantity) in the active number
   *  locale. Never currency-converted, no symbol. Integer by default; pass
   *  `maxDigits` for fractional quantities. */
  fmtNum: (value: MoneyArg, opts?: { minDigits?: number; maxDigits?: number }) => string;
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

  // All formatting delegates to the pure SSOT in lib/money.ts so panels and the
  // provider render identically.
  const convert = useCallback(
    (usd: number, snap?: FxSnapshot) => convertUsd(usd, currency, snap ?? fx),
    [currency, fx],
  );

  const fmt = useCallback(
    (usd: MoneyArg, snap?: FxSnapshot, digits = 2) =>
      formatMoney(usd, { currency, fx: snap ?? fx, digits }),
    [currency, fx],
  );

  const fmtPrice = useCallback(
    (usd: MoneyArg, snap?: FxSnapshot) => formatPrice(usd, { currency, fx: snap ?? fx }),
    [currency, fx],
  );

  const fmtPct = useCallback(
    (v: MoneyArg, opts?: { digits?: number; signed?: boolean }) =>
      formatPct(v, { currency, ...opts }),
    [currency],
  );

  const fmtNum = useCallback(
    (v: MoneyArg, opts?: { minDigits?: number; maxDigits?: number }) =>
      formatNumber(v, { currency, ...opts }),
    [currency],
  );

  const symbol = currency === "EUR" ? "€" : "$";

  const value = useMemo(
    () => ({ currency, setCurrency, fmt, fmtPrice, fmtPct, fmtNum, convert, symbol, fx, fxMeta }),
    [currency, setCurrency, fmt, fmtPrice, fmtPct, fmtNum, convert, symbol, fx, fxMeta],
  );

  return <Context.Provider value={value}>{children}</Context.Provider>;
}

export function useCurrency() {
  const c = useContext(Context);
  if (!c) throw new Error("useCurrency outside CurrencyProvider");
  return c;
}
