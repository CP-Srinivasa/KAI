// Single source of truth for money / price / percent rendering across the
// dashboard. Before this, panels each rolled their own toLocaleString/toFixed
// (en-US "$", de-DE " USD", bare toFixed without grouping) — four divergent
// representations of the same value. CurrencyProvider delegates to these pure
// functions and exposes them as hooks (fmt / fmtPrice / fmtPct), so every panel
// renders identically and respects the EUR/USD toggle from one place.
//
// Operator decision 2026-06-14: currency-aware EVERYWHERE — both capital amounts
// AND quoted instrument prices follow the active currency (converted via live FX).

import { toNum } from "@/lib/num";

export type Currency = "USD" | "EUR";
export type FxSnapshot = { USD: 1; EUR: number };

const EM_DASH = "—";

function localeFor(currency: Currency): string {
  return currency === "EUR" ? "de-DE" : "en-US";
}

function symbolFor(currency: Currency): string {
  return currency === "EUR" ? "€" : "$";
}

// EUR uses a trailing symbol ("1.234,56 €"), USD a leading one ("$1,234.56").
function withSymbol(formattedAbs: string, sign: string, currency: Currency): string {
  const sym = symbolFor(currency);
  return currency === "EUR" ? `${sign}${formattedAbs} ${sym}` : `${sign}${sym}${formattedAbs}`;
}

type FmtOpts = { currency: Currency; fx: FxSnapshot; digits?: number };

/** Convert a USD amount into the active currency (no formatting). */
export function convertUsd(usd: number, currency: Currency, fx: FxSnapshot): number {
  return usd * fx[currency];
}

/** Capital amounts: PnL, equity, cash, position value, exposure. Fixed digits
 *  (default 2), grouped, currency symbol. Returns em-dash for null/unparseable. */
export function formatMoney(
  usd: number | string | null | undefined,
  { currency, fx, digits = 2 }: FmtOpts,
): string {
  const n = toNum(usd);
  if (n === null) return EM_DASH;
  const value = convertUsd(n, currency, fx);
  const abs = Math.abs(value).toLocaleString(localeFor(currency), {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
  return withSymbol(abs, value < 0 ? "-" : "", currency);
}

/** Quoted instrument prices (entry / stop / target / market). Adaptive decimals
 *  so sub-cent micro-caps stay readable: >=1000 → 2, >=1 → 4, else 6. */
export function formatPrice(
  usd: number | string | null | undefined,
  { currency, fx }: Omit<FmtOpts, "digits">,
): string {
  const n = toNum(usd);
  if (n === null) return EM_DASH;
  const value = convertUsd(n, currency, fx);
  const abs = Math.abs(value);
  const digits = abs >= 1000 ? 2 : abs >= 1 ? 4 : 6;
  const body = Math.abs(value).toLocaleString(localeFor(currency), {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
  return withSymbol(body, value < 0 ? "-" : "", currency);
}

/** Compact money for tight tiles: ">=1000 → 1.2k", else integer. Currency-
 *  converted, with the active symbol. Decimal point stays "." (compact convention). */
export function formatMoneyCompact(
  usd: number | string | null | undefined,
  { currency, fx }: Omit<FmtOpts, "digits">,
): string {
  const n = toNum(usd);
  if (n === null) return EM_DASH;
  const value = convertUsd(n, currency, fx);
  const abs = Math.abs(value);
  const body = abs >= 1000 ? `${(abs / 1000).toFixed(1)}k` : abs.toFixed(0);
  return withSymbol(body, value < 0 ? "-" : "", currency);
}

/** Plain numbers that are NOT money: resolved-counts, n-values, instrument
 *  quantities. Follows the active number locale (de-DE/en-US grouping) so they
 *  match the money rendered alongside them under the currency toggle — but is
 *  never currency-converted and carries no symbol. Defaults to a grouped integer
 *  (0 fraction digits); pass `maxDigits` for fractional quantities. Returns
 *  em-dash for null/unparseable. */
export function formatNumber(
  v: number | string | null | undefined,
  { currency, minDigits = 0, maxDigits = 0 }: { currency: Currency; minDigits?: number; maxDigits?: number },
): string {
  const n = toNum(v);
  if (n === null) return EM_DASH;
  return n.toLocaleString(localeFor(currency), {
    minimumFractionDigits: minDigits,
    maximumFractionDigits: Math.max(minDigits, maxDigits),
  });
}

/** Percent — never currency-converted, but follows the active number locale so
 *  it matches the money around it. `signed` prefixes a "+" on positives. */
export function formatPct(
  v: number | string | null | undefined,
  { currency, digits = 1, signed = false }: { currency: Currency; digits?: number; signed?: boolean },
): string {
  const n = toNum(v);
  if (n === null) return EM_DASH;
  const sign = signed && n > 0 ? "+" : "";
  return `${sign}${n.toLocaleString(localeFor(currency), {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}%`;
}
