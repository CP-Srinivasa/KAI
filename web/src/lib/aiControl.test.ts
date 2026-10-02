import { afterEach, describe, expect, it, vi } from "vitest";

import { _resetInflightForTests } from "./api";
import { STATE_META, fetchAiControl, formatTokens, formatUsd, runwayLabel } from "./aiControl";

afterEach(() => {
  vi.restoreAllMocks();
  _resetInflightForTests();
});

describe("aiControl", () => {
  it("kennt jeden Zustand mit Symbol", () => {
    for (const s of ["aktiv", "bereit", "pausiert", "gestoert", "ausser_kraft", "deaktiviert"] as const) {
      expect(STATE_META[s].symbol.length).toBeGreaterThan(0);
    }
    expect(STATE_META.gestoert.tone).toBe("neg");
  });

  it("formatiert ehrlich", () => {
    expect(formatUsd(null)).toBe("–");
    expect(formatUsd(1.2345)).toBe("1,23 $");
    expect(formatTokens(312000)).toBe("312k");
    expect(formatTokens(999)).toBe("999");
    expect(runwayLabel(null)).toBe("∞");
    expect(runwayLabel(8.6)).toBe("~9 Tage");
  });

  it("holt den Vertrag", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ schema: "ai-control/v1", attention: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const data = await fetchAiControl();
    expect(data.schema).toBe("ai-control/v1");
  });
});
