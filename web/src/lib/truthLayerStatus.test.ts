import { describe, expect, it } from "vitest";
import { deriveTruthLayer, truthLayerStateToStatus } from "./truthLayerStatus";
import type { DashboardQuality } from "@/lib/api";

function q(
  contract: Record<string, unknown> | undefined,
  version: number | undefined = 2,
): DashboardQuality {
  const mc =
    contract === undefined
      ? undefined
      : Object.fromEntries(
          Object.entries(contract).map(([k, value]) => [
            k,
            { value, unit: "x" } as DashboardQuality["metric_contract"][string],
          ]),
        );
  return {
    dashboard_truth_contract_version: version,
    metric_contract: mc,
  } as DashboardQuality;
}

describe("deriveTruthLayer", () => {
  it("alle Metriken aufgelöst = ok", () => {
    const s = deriveTruthLayer(q({ a: 179, b: -8.66, c: "read_only", d: 0 }));
    expect(s.state).toBe("ok");
    expect(s.resolved).toBe(4);
    expect(s.total).toBe(4);
    expect(s.version).toBe(2);
  });
  it("0 zählt als aufgelöst (kein truthy-Check)", () => {
    const s = deriveTruthLayer(q({ source_reliability: 0 }));
    expect(s.state).toBe("ok");
    expect(s.resolved).toBe(1);
  });
  it("teilweise null = degraded", () => {
    const s = deriveTruthLayer(q({ a: 1, b: null, c: undefined }));
    expect(s.state).toBe("degraded");
    expect(s.resolved).toBe(1);
    expect(s.total).toBe(3);
  });
  it("kein/leerer Vertrag = no_contract (ehrlich, nicht ok)", () => {
    expect(deriveTruthLayer(q(undefined)).state).toBe("no_contract");
    expect(deriveTruthLayer(q({})).state).toBe("no_contract");
    expect(deriveTruthLayer(null).state).toBe("no_contract");
  });
  it("Version durchgereicht auch ohne Vertrag", () => {
    expect(deriveTruthLayer(q(undefined, 3)).version).toBe(3);
  });
});

describe("truthLayerStateToStatus", () => {
  it("ok = operational, degraded = degraded, no_contract = unverified", () => {
    expect(truthLayerStateToStatus("ok")).toBe("operational");
    expect(truthLayerStateToStatus("degraded")).toBe("degraded");
    expect(truthLayerStateToStatus("no_contract")).toBe("unverified");
  });
});
