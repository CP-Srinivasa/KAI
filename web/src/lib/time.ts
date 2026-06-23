// Date/time locale — single source of truth. #271 centralised the NUMBER locale
// in money.ts (localeFor), but date formatting stayed scattered as hardcoded
// "de-DE" toLocale* calls across panels (Portfolio, PremiumSignalTrail,
// PremiumTradeCard, this file). This constant + the semantic helpers below are
// the date counterpart: every panel renders dates identically and the locale
// changes in exactly ONE place. Dates are not currency-bound, so this is a fixed
// locale (unlike money.ts which is currency-aware).
export const DATE_LOCALE = "de-DE";

const DE_RELATIVE = new Intl.RelativeTimeFormat(DATE_LOCALE, { numeric: "auto" });

const DAY_TIME_OPTS: Intl.DateTimeFormatOptions = {
  day: "2-digit",
  month: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
};
const CLOCK_OPTS: Intl.DateTimeFormatOptions = { hour: "2-digit", minute: "2-digit" };
const SHORT_DATE_OPTS: Intl.DateTimeFormatOptions = { day: "2-digit", month: "2-digit" };

export function parseIso(value: string | null | undefined): Date | null {
  if (!value) return null;
  const ms = Date.parse(value);
  return Number.isFinite(ms) ? new Date(ms) : null;
}

export function formatRelative(value: string | null | undefined, now: Date = new Date()): string {
  const parsed = parseIso(value);
  if (!parsed) return "—";
  const deltaMs = parsed.getTime() - now.getTime();
  const absSec = Math.abs(deltaMs) / 1000;
  if (absSec < 45) return DE_RELATIVE.format(Math.round(deltaMs / 1000), "second");
  if (absSec < 3600) return DE_RELATIVE.format(Math.round(deltaMs / 60000), "minute");
  if (absSec < 86400) return DE_RELATIVE.format(Math.round(deltaMs / 3600000), "hour");
  if (absSec < 2592000) return DE_RELATIVE.format(Math.round(deltaMs / 86400000), "day");
  return DE_RELATIVE.format(Math.round(deltaMs / 2592000000), "month");
}

export function formatAbsolute(value: string | null | undefined): string {
  const parsed = parseIso(value);
  if (!parsed) return "—";
  return parsed.toISOString().substring(0, 19).replace("T", " ") + "Z";
}

export function formatDuration(fromIso: string | null | undefined, toIso: string | null | undefined): string {
  const from = parseIso(fromIso);
  const to = parseIso(toIso);
  if (!from || !to) return "—";
  const deltaSec = Math.max(0, (to.getTime() - from.getTime()) / 1000);
  if (deltaSec < 60) return `+${Math.round(deltaSec)}s`;
  if (deltaSec < 3600) return `+${Math.round(deltaSec / 60)}m`;
  if (deltaSec < 86400) return `+${Math.round(deltaSec / 3600)}h`;
  return `+${Math.round(deltaSec / 86400)}d`;
}

/** Short timestamp "DD.MM., HH:MM" in the dashboard date locale. Em-dash for
 *  empty input, the raw string for unparseable input (callers showed raw ISO). */
export function formatDayTime(value: string | null | undefined): string {
  if (!value) return "—";
  const d = parseIso(value);
  if (!d) return value;
  return d.toLocaleString(DATE_LOCALE, DAY_TIME_OPTS);
}

/** Clock time "HH:MM" in the dashboard date locale. */
export function formatClock(value: string | null | undefined): string {
  const d = parseIso(value);
  if (!d) return value ?? "—";
  return d.toLocaleTimeString(DATE_LOCALE, CLOCK_OPTS);
}

/** Short date "DD.MM." in the dashboard date locale. */
export function formatShortDate(value: string | null | undefined): string {
  const d = parseIso(value);
  if (!d) return value ?? "—";
  return d.toLocaleDateString(DATE_LOCALE, SHORT_DATE_OPTS);
}
