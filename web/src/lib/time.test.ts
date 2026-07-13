import { describe, expect, it } from "vitest";
import {
  DATE_LOCALE,
  formatAbsolute,
  formatBerlinDate,
  formatClock,
  formatDayTime,
  formatDuration,
  formatRelative,
  formatShortDate,
  parseIso,
} from "./time";

// Midday UTC so the local-time date part can't roll across midnight in the
// test runner's timezone — keeps the date assertions stable everywhere.
const ISO = "2026-06-15T12:00:00Z";

describe("DATE_LOCALE — single source of truth", () => {
  it("is the German dashboard locale", () => {
    expect(DATE_LOCALE).toBe("de-DE");
  });
});

describe("formatDayTime — short timestamp", () => {
  it("renders day, month, hour and minute (de-DE has a dot-separated date)", () => {
    const out = formatDayTime(ISO);
    // de-DE date uses ".", time uses ":" — both present, not the en-US "/" form.
    expect(out).toContain(".");
    expect(out).toContain(":");
    expect(out).not.toContain("/");
  });
  it("returns em-dash for empty and the raw string for unparseable input", () => {
    expect(formatDayTime(null)).toBe("—");
    expect(formatDayTime(undefined)).toBe("—");
    expect(formatDayTime("")).toBe("—");
    expect(formatDayTime("not-a-date")).toBe("not-a-date");
  });
});

describe("formatClock — HH:MM", () => {
  it("renders a two-digit clock", () => {
    expect(formatClock(ISO)).toMatch(/^\d{2}:\d{2}$/);
  });
  it("falls back to the raw string for unparseable input", () => {
    expect(formatClock("nope")).toBe("nope");
    expect(formatClock(null)).toBe("—");
  });
});

describe("formatShortDate — DD.MM.", () => {
  it("renders a de-DE short date (trailing dot, no slash)", () => {
    expect(formatShortDate(ISO)).toMatch(/^\d{2}\.\d{2}\.$/);
  });
  it("falls back to the raw string for unparseable input", () => {
    expect(formatShortDate("nope")).toBe("nope");
    expect(formatShortDate(null)).toBe("—");
  });
});

describe("existing helpers still behave (regression)", () => {
  it("parseIso parses valid ISO and rejects junk", () => {
    expect(parseIso(ISO)).toBeInstanceOf(Date);
    expect(parseIso("junk")).toBeNull();
    expect(parseIso(null)).toBeNull();
  });
  it("formatAbsolute emits a UTC string", () => {
    expect(formatAbsolute(ISO)).toBe("2026-06-15 12:00:00Z");
    expect(formatAbsolute(null)).toBe("—");
  });
  it("formatRelative is non-empty for a valid instant", () => {
    const now = new Date("2026-06-15T12:01:00Z");
    expect(formatRelative(ISO, now)).not.toBe("—");
    expect(formatRelative(null)).toBe("—");
  });
  it("formatDuration renders a compact +Ns/+Nm/+Nh/+Nd", () => {
    expect(formatDuration(ISO, "2026-06-15T12:00:30Z")).toBe("+30s");
    expect(formatDuration(ISO, "2026-06-15T12:05:00Z")).toBe("+5m");
    expect(formatDuration(null, ISO)).toBe("—");
  });
});

describe("formatBerlinDate — canonical German date, fixed Europe/Berlin", () => {
  it("renders the paper-epoch reset (22:22:09Z on the 12th) as 13.07.2026 in Berlin (CEST)", () => {
    // Deterministic regardless of the runner's/viewer's timezone: 22:22 UTC on
    // 2026-07-12 is already 2026-07-13 00:22 in Berlin summer time.
    expect(formatBerlinDate("2026-07-12T22:22:09.568711+00:00")).toBe("13.07.2026");
  });
  it("keeps a same-day UTC timestamp on its Berlin calendar day with 2-digit parts", () => {
    expect(formatBerlinDate("2026-01-05T10:00:00Z")).toBe("05.01.2026");
  });
  it("returns an em-dash for empty input", () => {
    expect(formatBerlinDate(null)).toBe("—");
    expect(formatBerlinDate(undefined)).toBe("—");
  });
});
