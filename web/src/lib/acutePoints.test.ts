import { describe, expect, it } from "vitest";
import { acuteChips, recommendedAction } from "./acutePoints";
import type { TruthChip } from "@/lib/truthStatus";

function chip(key: string, tone: TruthChip["tone"]): TruthChip {
  return { key, label: key, value: "x", tone, hint: "h" };
}

describe("acuteChips", () => {
  it("behält nur kritisch/warn, Reihenfolge bleibt", () => {
    const chips = [
      chip("a", "critical"),
      chip("b", "info"),
      chip("c", "warn"),
      chip("d", "ok"),
      chip("e", "readonly"),
      chip("f", "muted"),
    ];
    expect(acuteChips(chips).map((c) => c.key)).toEqual(["a", "c"]);
  });
  it("leer wenn nichts akut", () => {
    expect(acuteChips([chip("x", "ok"), chip("y", "muted")])).toEqual([]);
  });
});

describe("recommendedAction", () => {
  it("kennt die wichtigsten Chip-Keys", () => {
    expect(recommendedAction("entry-mode")).toMatch(/Schutzschalter/);
    expect(recommendedAction("source")).toMatch(/trusted/);
    expect(recommendedAction("priority")).toMatch(/Heartbeat/);
  });
  it("Fallback für unbekannte Keys", () => {
    expect(recommendedAction("etwas-neues")).toBe("Status prüfen und Ursache klären.");
  });
});
