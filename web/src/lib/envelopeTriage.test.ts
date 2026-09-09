import { describe, it, expect } from "vitest";
import { windowCounts, lastReceivedAt, TRIAGE_WINDOW_DAYS } from "./envelopeTriage";

const NOW = Date.parse("2026-09-09T08:00:00Z");

describe("windowCounts", () => {
  it("counts inside the window and ignores what falls outside", () => {
    const recs = [
      { timestamp_utc: "2026-09-08T10:00:00Z", status: "ok" },
      { timestamp_utc: "2026-09-01T10:00:00Z", status: "duplicate" },
      { timestamp_utc: "2026-06-01T10:00:00Z", status: "ok" }, // ausserhalb
    ];
    const c = windowCounts(recs, TRIAGE_WINDOW_DAYS, NOW);
    expect(c.accepted).toBe(1);
    expect(c.duplicate).toBe(1);
  });

  it("a 30-day window still sees what a today-window would have missed", () => {
    // Genau der Fall, der die Seite tot aussehen liess: das juengste Ereignis
    // liegt Wochen zurueck, aber es gibt eines.
    const recs = [{ timestamp_utc: "2026-09-02T09:00:00Z", status: "ok" }];
    expect(windowCounts(recs, 1, NOW).accepted).toBe(0);
    expect(windowCounts(recs, 30, NOW).accepted).toBe(1);
  });

  it("skips records without a usable timestamp instead of counting them", () => {
    const recs = [
      { timestamp_utc: null, status: "ok" },
      { timestamp_utc: "kaputt", status: "ok" },
    ];
    expect(windowCounts(recs, 30, NOW).accepted).toBe(0);
  });
});

describe("lastReceivedAt", () => {
  it("returns the newest timestamp", () => {
    expect(
      lastReceivedAt([
        { timestamp_utc: "2026-07-31T12:00:00Z" },
        { timestamp_utc: "2026-08-15T12:00:00Z" },
        { timestamp_utc: "2026-06-01T12:00:00Z" },
      ]),
    ).toBe("2026-08-15T12:00:00Z");
  });

  it("returns null on an empty list — no invented zero", () => {
    expect(lastReceivedAt([])).toBeNull();
  });
});
