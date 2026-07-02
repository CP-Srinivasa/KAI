// Vitest unit tests for KAI state resolver.
// Spec source: docs/kai_persona/technical_ui_pack_v3_2.md §17.1

import { describe, it, expect } from "vitest";
import { resolveKaiState, createFallbackState, failClosedState, isValidKaiState } from "../stateResolver";
import { KAI_STATE_PRIORITY } from "../constants";
import type { KaiRuntimeState } from "../types";

function rt(state: KaiRuntimeState["state"], comment = "test"): KaiRuntimeState {
  return {
    state,
    severity: "info",
    priority: KAI_STATE_PRIORITY[state],
    statusLabel: state,
    color: "#000",
    icon: "kai_test",
    animation: "test",
    comment,
    timestamp: "2026-05-03T00:00:00Z",
  };
}

describe("resolveKaiState — priority order", () => {
  it("ERROR overrides WARNING", () => {
    const winner = resolveKaiState([rt("WARNING"), rt("ERROR")]);
    expect(winner.state).toBe("ERROR");
  });

  it("WARNING overrides SIGNAL", () => {
    const winner = resolveKaiState([rt("SIGNAL"), rt("WARNING")]);
    expect(winner.state).toBe("WARNING");
  });

  it("SIGNAL overrides SECURITY", () => {
    const winner = resolveKaiState([rt("SECURITY"), rt("SIGNAL")]);
    expect(winner.state).toBe("SIGNAL");
  });

  it("SECURITY overrides ANALYSIS", () => {
    const winner = resolveKaiState([rt("ANALYSIS"), rt("SECURITY")]);
    expect(winner.state).toBe("SECURITY");
  });

  it("ANALYSIS overrides IDLE", () => {
    const winner = resolveKaiState([rt("IDLE"), rt("ANALYSIS")]);
    expect(winner.state).toBe("ANALYSIS");
  });

  it("OFFLINE has lowest priority", () => {
    const winner = resolveKaiState([rt("OFFLINE"), rt("IDLE")]);
    expect(winner.state).toBe("IDLE");
  });

  it("empty input -> OFFLINE fallback", () => {
    const winner = resolveKaiState([]);
    expect(winner.state).toBe("OFFLINE");
    expect(winner.source).toBe("fallback");
  });

  it("preserves the runtime payload of the winner", () => {
    const winner = resolveKaiState([rt("IDLE", "calm"), rt("ERROR", "fire")]);
    expect(winner.state).toBe("ERROR");
    expect(winner.comment).toBe("fire");
  });
});

describe("createFallbackState", () => {
  it("returns a properly typed runtime state", () => {
    const state = createFallbackState("OFFLINE", "no signal");
    expect(state.state).toBe("OFFLINE");
    expect(state.priority).toBe(KAI_STATE_PRIORITY.OFFLINE);
    expect(state.comment).toBe("no signal");
  });

  it("ERROR fallback gets critical severity", () => {
    const state = createFallbackState("ERROR", "explode");
    expect(state.severity).toBe("critical");
  });
});

describe("failClosedState", () => {
  it("forces ERROR with critical severity and audit-friendly source", () => {
    const state = failClosedState("config invalid");
    expect(state.state).toBe("ERROR");
    expect(state.severity).toBe("critical");
    expect(state.source).toBe("fail_closed_guard");
    expect(state.comment).toContain("config invalid");
  });
});

describe("isValidKaiState", () => {
  it("accepts the 7 known states", () => {
    for (const s of ["IDLE", "ANALYSIS", "SIGNAL", "WARNING", "SECURITY", "ERROR", "OFFLINE"]) {
      expect(isValidKaiState(s)).toBe(true);
    }
  });

  it("rejects unknown values", () => {
    expect(isValidKaiState("PARTY")).toBe(false);
    expect(isValidKaiState(123)).toBe(false);
    expect(isValidKaiState(null)).toBe(false);
    expect(isValidKaiState(undefined)).toBe(false);
  });
});
