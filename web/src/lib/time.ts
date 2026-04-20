const DE_RELATIVE = new Intl.RelativeTimeFormat("de-DE", { numeric: "auto" });

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
