import { describe, expect, it } from "vitest";
import {
  DATE_LOCALE,
  formatAbsolute,
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
