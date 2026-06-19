import { describe, expect, it } from "vitest";
import { integrityStateToStatus } from "./AuditIntegrityKpi";

describe("integrityStateToStatus", () => {
  it("ok mit Proof = verifiziert, ohne Proof nur ausstehend", () => {
    expect(integrityStateToStatus("ok", true)).toBe("verified");
    expect(integrityStateToStatus("ok", false)).toBe("pending");
  });
  it("no_anchor = ausstehend, unavailable = degradiert", () => {
    expect(integrityStateToStatus("no_anchor", false)).toBe("pending");
    expect(integrityStateToStatus("unavailable", false)).toBe("degraded");
  });
  it("unbekannter State = unverifiziert (ehrlich, nicht erfunden)", () => {
    expect(integrityStateToStatus("etwas", false)).toBe("unverified");
  });
});
