import { describe, expect, it } from "vitest";
import { auditChainStateToStatus } from "./AuditChainKpi";

describe("auditChainStateToStatus", () => {
  it("ok = verifiziert (tamper-frei)", () => {
    expect(auditChainStateToStatus("ok")).toBe("verified");
  });
  it("broken = kritisch (Tamper darf nie grün/ruhig erscheinen)", () => {
    expect(auditChainStateToStatus("broken")).toBe("critical");
  });
  it("unavailable = degradiert", () => {
    expect(auditChainStateToStatus("unavailable")).toBe("degraded");
  });
  it("unbekannter/empty State = unverifiziert (ehrlich, nicht erfunden)", () => {
    expect(auditChainStateToStatus("empty")).toBe("unverified");
    expect(auditChainStateToStatus("etwas")).toBe("unverified");
  });
});
