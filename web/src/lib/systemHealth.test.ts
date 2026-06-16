import { describe, expect, it } from "vitest";
import { timerStateToStatus } from "./systemHealth";
import type { TimerHealthResponse } from "@/lib/api";

describe("timerStateToStatus", () => {
  const cases: Array<[TimerHealthResponse["state"], string]> = [
    ["ok", "operational"],
    ["has_inactive", "degraded"],
    ["stale", "stale"],
    ["no_data", "unverified"],
    ["corrupt", "critical"],
    ["critical", "critical"],
  ];
  it.each(cases)("%s → %s", (state, expected) => {
    expect(timerStateToStatus(state)).toBe(expected);
  });
});
