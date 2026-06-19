// Truth-Layer-Status (#314, Truth-Layer-Slice / Konzept §8/§9). Reine Ableitung
// aus dem bereits gepollten /dashboard/api/quality (KEIN neuer Endpoint): wie
// viele der kanonischen metric_contract-Metriken auflösen (value != null) vs.
// gesamt, plus die Truth-Contract-Version. EHRLICH: ohne Vertrag = no_contract
// (nicht verifizierbar), nicht „ok". Pure/testbar (truthLayerStatus.test.ts).
import type { DashboardQuality } from "@/lib/api";
import type { StatusKind } from "@/lib/status";

export type TruthLayerState = "ok" | "degraded" | "no_contract";

export type TruthLayerSummary = {
  state: TruthLayerState;
  resolved: number;
  total: number;
  version: number | null;
};

/** Pure: quality-Payload → Truth-Layer-Zusammenfassung. resolved zählt
 *  value !== null/undefined — 0 ist ein gültiger aufgelöster Wert, NICHT
 *  unaufgelöst (kein truthy-Check). */
export function deriveTruthLayer(quality: DashboardQuality | null): TruthLayerSummary {
  const version = quality?.dashboard_truth_contract_version ?? null;
  const mc = quality?.metric_contract;
  if (!mc || Object.keys(mc).length === 0) {
    return { state: "no_contract", resolved: 0, total: 0, version };
  }
  const entries = Object.values(mc);
  const total = entries.length;
  const resolved = entries.filter((m) => m.value !== null && m.value !== undefined).length;
  return { state: resolved === total ? "ok" : "degraded", resolved, total, version };
}

/** Truth-Layer-State → kanonischer StatusKind. no_contract = unverifiziert
 *  (ehrlich: ohne Vertrag nichts belegt), nicht „ok". Pure/testbar. */
export function truthLayerStateToStatus(state: TruthLayerState): StatusKind {
  switch (state) {
    case "ok":
      return "operational";
    case "degraded":
      return "degraded";
    default:
      return "unverified"; // no_contract
  }
}
