import { describe, expect, it } from "vitest";
import { nextDensity, modeTone } from "./AppState";

describe("nextDensity", () => {
  it("schaltet zwischen kompakt und komfortabel um", () => {
    expect(nextDensity("comfortable")).toBe("compact");
    expect(nextDensity("compact")).toBe("comfortable");
  });
  it("ist involutiv (zweimal = Ausgang)", () => {
    expect(nextDensity(nextDensity("compact"))).toBe("compact");
  });
});

describe("modeTone (Regression)", () => {
  it("live=pos, sim=info, paper=warn", () => {
    expect(modeTone("live")).toBe("pos");
    expect(modeTone("sim")).toBe("info");
    expect(modeTone("paper")).toBe("warn");
  });
});
