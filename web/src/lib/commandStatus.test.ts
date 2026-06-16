import { describe, expect, it } from "vitest";
import {
  backendHealthToStatus,
  kaiStateToStatus,
  truthToneToStatusTone,
} from "./commandStatus";
import type { KaiState } from "@/kai/types";
import type { BackendStatus } from "@/lib/useBackendHealth";
import type { TruthTone } from "@/lib/truthStatus";

describe("kaiStateToStatus", () => {
  const cases: Array<[KaiState, string]> = [
    ["IDLE", "idle"],
    ["ANALYSIS", "active"],
    ["SIGNAL", "active"],
    ["WARNING", "degraded"],
    ["SECURITY", "urgent"],
    ["ERROR", "critical"],
    ["OFFLINE", "fail-closed"],
  ];
  it.each(cases)("%s → %s", (state, expected) => {
    expect(kaiStateToStatus(state)).toBe(expected);
  });
});

describe("backendHealthToStatus", () => {
  const cases: Array<[BackendStatus["state"], string]> = [
    ["connected", "operational"],
    ["checking", "pending"],
    ["unauthorized", "blocked"],
    ["offline", "critical"],
  ];
  it.each(cases)("%s → %s", (state, expected) => {
    expect(backendHealthToStatus(state)).toBe(expected);
  });
});

describe("truthToneToStatusTone", () => {
  const cases: Array<[TruthTone, string]> = [
    ["critical", "neg"],
    ["warn", "warn"],
    ["info", "info"],
    ["readonly", "muted"],
    ["ok", "pos"],
    ["muted", "muted"],
  ];
  it.each(cases)("%s → %s", (tone, expected) => {
    expect(truthToneToStatusTone(tone)).toBe(expected);
  });
});
