import { describe, expect, it } from "vitest";
import { replayStateToStatus } from "./ReplayStatusKpi";

describe("replayStateToStatus", () => {
  it("ok = operational (gesund)", () => {
    expect(replayStateToStatus("ok")).toBe("operational");
  });
  it("degraded und unavailable = degraded (Achtung, kein roter Alarm)", () => {
    expect(replayStateToStatus("degraded")).toBe("degraded");
    expect(replayStateToStatus("unavailable")).toBe("degraded");
  });
  it("warming/unbekannt = pending (ehrlich lädt, nicht erfunden)", () => {
    expect(replayStateToStatus("warming")).toBe("pending");
    expect(replayStateToStatus("etwas")).toBe("pending");
  });
});
