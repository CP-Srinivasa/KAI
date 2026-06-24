import { describe, expect, it } from "vitest";
import { integrityStateToStatus } from "./AuditIntegrityKpi";

describe("integrityStateToStatus", () => {
  it("ok: nur Bitcoin-confirmed = verifiziert; pending/ohne Proof nur ausstehend", () => {
    // Ehrlich: ein nur eingereichter (pending) OTS-Proof ist NICHT verifiziert,
    // erst die Bitcoin-Attestation zählt als 'verified'.
    expect(integrityStateToStatus("ok", "confirmed")).toBe("verified");
    expect(integrityStateToStatus("ok", "pending")).toBe("pending");
    expect(integrityStateToStatus("ok", "")).toBe("pending");
  });
  it("no_anchor = ausstehend, unavailable = degradiert", () => {
    expect(integrityStateToStatus("no_anchor", "")).toBe("pending");
    expect(integrityStateToStatus("unavailable", "")).toBe("degraded");
  });
  it("unbekannter State = unverifiziert (ehrlich, nicht erfunden)", () => {
    expect(integrityStateToStatus("etwas", "")).toBe("unverified");
  });
});
