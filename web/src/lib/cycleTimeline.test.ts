import { describe, expect, it } from "vitest";
import { cycleTone, cyclesToRail } from "./cycleTimeline";
import type { TradingCycle } from "@/lib/api";

function cyc(status: string): TradingCycle {
  return { status } as TradingCycle;
}

describe("cycleTone", () => {
  it("mappt Ausgänge auf Töne", () => {
    expect(cycleTone("completed")).toBe("pos");
    expect(cycleTone("no_signal")).toBe("neutral");
    expect(cycleTone("order_failed")).toBe("neg");
    expect(cycleTone("risk_blocked")).toBe("warn");
    expect(cycleTone("irgendwas")).toBe("info");
  });
});

describe("cyclesToRail", () => {
  it("erzeugt ein RailItem je Cycle mit korrektem Ton + eindeutigem key", () => {
    const rail = cyclesToRail([cyc("completed"), cyc("no_signal"), cyc("order_failed")]);
    expect(rail).toHaveLength(3);
    expect(rail.map((r) => r.tone)).toEqual(["pos", "neutral", "neg"]);
    expect(new Set(rail.map((r) => r.key)).size).toBe(3);
  });
  it("leer → []", () => {
    expect(cyclesToRail([])).toEqual([]);
  });
});
