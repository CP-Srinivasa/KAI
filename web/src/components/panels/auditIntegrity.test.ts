import { describe, expect, it } from "vitest";
import { integrityLabel, integrityStateToStatus } from "./AuditIntegrityKpi";

describe("integrityStateToStatus", () => {
  it("ok: verifiziert NUR mit confirmed UND Bitcoin-Prüfung (Audit A3)", () => {
    // Ehrlich: eine Bitcoin-Attestation im Proof ist nur behauptet — erst die
    // Prüfung gegen den echten Blockheader (für genau diesen Inhalt) zählt.
    expect(integrityStateToStatus("ok", "confirmed", "verified")).toBe("verified");
    expect(integrityStateToStatus("ok", "confirmed")).toBe("pending");
    expect(integrityStateToStatus("ok", "confirmed", "unverified")).toBe("pending");
    expect(integrityStateToStatus("ok", "confirmed", "not_attested")).toBe("pending");
  });
  it("ok: ein widersprechender Beweis ist kritisch, ein nicht prüfbarer degradiert", () => {
    expect(integrityStateToStatus("ok", "confirmed", "mismatch")).toBe("critical");
    expect(integrityStateToStatus("ok", "confirmed", "unverifiable")).toBe("degraded");
  });
  it("ok: pending/ohne Proof nur ausstehend — auch mit irreführendem Prüffeld", () => {
    expect(integrityStateToStatus("ok", "pending", "verified")).toBe("pending");
    expect(integrityStateToStatus("ok", "", "")).toBe("pending");
  });
  it("no_anchor = ausstehend, unavailable = degradiert", () => {
    expect(integrityStateToStatus("no_anchor", "")).toBe("pending");
    expect(integrityStateToStatus("unavailable", "")).toBe("degraded");
  });
  it("unbekannter State = unverifiziert (ehrlich, nicht erfunden)", () => {
    expect(integrityStateToStatus("etwas", "")).toBe("unverified");
  });
});

describe("integrityLabel", () => {
  it("unterscheidet geprüft, ungültig, nicht prüfbar und ausstehend", () => {
    expect(integrityLabel("ok", "confirmed", "verified", true)).toBe("Bitcoin-geprüft");
    expect(integrityLabel("ok", "confirmed", "mismatch", true)).toBe("Beweis UNGÜLTIG");
    expect(integrityLabel("ok", "confirmed", "unverifiable", true)).toBe("nicht prüfbar");
    expect(integrityLabel("ok", "confirmed", "unverified", true)).toBe(
      "bestätigt, Bitcoin-Prüfung ausstehend",
    );
  });
  it("pending, Proof ohne Attestation und kein Anchor", () => {
    expect(integrityLabel("ok", "pending", "", true)).toBe("OTS pending");
    expect(integrityLabel("ok", "", "", true)).toBe("OTS-Proof");
    expect(integrityLabel("ok", "", "", false)).toBe("aufgezeichnet");
    expect(integrityLabel("no_anchor", "", "", false)).toBe("kein Anchor");
  });
});
