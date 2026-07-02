import { describe, it, expect } from "vitest";
import { nGateTone } from "./NOverviewPanel";

describe("nGateTone", () => {
  it("muted when no value yet (honest absence)", () => {
    expect(nGateTone(null, false)).toBe("muted");
  });

  it("pos once the gate threshold is reached", () => {
    expect(nGateTone(100, true)).toBe("pos");
    expect(nGateTone(140, true)).toBe("pos");
  });

  it("warn in the upper approach band (50–99 %)", () => {
    expect(nGateTone(77, false)).toBe("warn");
    expect(nGateTone(50, false)).toBe("warn");
  });

  it("neg far below threshold (<50 %)", () => {
    expect(nGateTone(25, false)).toBe("neg");
    expect(nGateTone(0, false)).toBe("neg");
  });
});
