import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

export type Currency = "USD" | "EUR";
export type FxSnapshot = { USD: 1; EUR: number };

// USD is the base. EUR-per-USD live rate. Bis ein Operator-FX-Endpoint existiert
// halten wir einen konservativen statischen Kurs — keine Demo-Werte, keine Drift.
export const currentFx: FxSnapshot = { USD: 1, EUR: 0.921 };

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
};

const Context = createContext<Ctx | null>(null);
const KEY = "kai-currency";

function initial(): Currency {
  try {
    const v = localStorage.getItem(KEY);
    if (v === "USD" || v === "EUR") return v;
  } catch {}
  if (typeof navigator !== "undefined" && navigator.language?.toLowerCase().startsWith("de")) return "EUR";
  return "USD";
}

export function CurrencyProvider({ children }: { children: ReactNode }) {
  const [currency, _set] = useState<Currency>(initial);

  useEffect(() => {
    try {
      localStorage.setItem(KEY, currency);
    } catch {}
  }, [currency]);

  const setCurrency = useCallback((c: Currency) => _set(c), []);

  const convert = useCallback(
    (usd: number, fx?: FxSnapshot) => {
      const rate = (fx ?? currentFx)[currency];
      return usd * rate;
    },
    [currency],
  );

  const fmt = useCallback(
    (usd: number, fx?: FxSnapshot, digits = 2) => {
      const value = convert(usd, fx);
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
    () => ({ currency, setCurrency, fmt, convert, symbol }),
    [currency, setCurrency, fmt, convert, symbol],
  );

  return <Context.Provider value={value}>{children}</Context.Provider>;
}

export function useCurrency() {
  const c = useContext(Context);
  if (!c) throw new Error("useCurrency outside CurrencyProvider");
  return c;
}
