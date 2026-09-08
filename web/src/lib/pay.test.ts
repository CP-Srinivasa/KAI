import { describe, expect, it } from "vitest";
import { ApiError } from "@/lib/api";
import {
  describeError,
  formatCountdown,
  formatSats,
  isPayDisabledError,
  isTerminalStatus,
  normalizePayList,
  payStatusLabel,
  payStatusTone,
  qrPayload,
  remainingSeconds,
  toPayRequest,
  validatePayForm,
} from "./pay";

describe("KAI PAY — Statusabbildung", () => {
  it("bildet die vier Vertragszustände auf Töne ab; Unbekanntes wird muted, nie grün", () => {
    expect(payStatusTone("WAITING")).toBe("warn");
    expect(payStatusTone("SETTLED")).toBe("pos");
    expect(payStatusTone("EXPIRED")).toBe("neg");
    expect(payStatusTone("FAILED")).toBe("neg");
    expect(payStatusTone("SOMETHING_NEW")).toBe("muted");
    expect(payStatusTone(null)).toBe("muted");
  });

  it("Endzustände sind genau SETTLED|EXPIRED|FAILED", () => {
    expect(isTerminalStatus("WAITING")).toBe(false);
    expect(isTerminalStatus("SETTLED")).toBe(true);
    expect(isTerminalStatus("EXPIRED")).toBe(true);
    expect(isTerminalStatus("FAILED")).toBe(true);
    expect(isTerminalStatus(undefined)).toBe(false);
  });

  it("Label sind die Vertragswörter selbst", () => {
    expect(payStatusLabel("SETTLED")).toBe("SETTLED");
    expect(payStatusLabel(null)).toBe("UNBEKANNT");
  });

  it("404 auf /pay/health heißt deaktiviert; andere Fehler nicht", () => {
    expect(isPayDisabledError({ kind: "not_found", status: 404 })).toBe(true);
    expect(isPayDisabledError({ kind: "server", status: 500 })).toBe(false);
    expect(isPayDisabledError(null)).toBe(false);
  });
});

describe("KAI PAY — Formular gegen den Vertrag", () => {
  it("nimmt nur ganze sat-Beträge ≥ 1 — '5.00', '0', '' und '-5' scheitern", () => {
    for (const bad of ["5.00", "5,5", "0", "", "-5", "1e3", "abc"]) {
      const r = validatePayForm({ amountSat: bad, description: "Test", reference: "" });
      expect(r.ok, `amount ${JSON.stringify(bad)}`).toBe(false);
      if (!r.ok) expect(r.errors.amountSat).toMatch(/ganze Zahl/);
    }
  });

  it("baut den POST-Body: reference nur, wenn gesetzt", () => {
    const r = validatePayForm({ amountSat: " 5000 ", description: " Test ", reference: "  " });
    expect(r).toEqual({ ok: true, body: { amount_sat: 5000, description: "Test" } });
    const r2 = validatePayForm({ amountSat: "1", description: "x", reference: "inv-42" });
    expect(r2).toEqual({ ok: true, body: { amount_sat: 1, description: "x", reference: "inv-42" } });
  });

  it("Beschreibung 1..140, Referenz ≤ 64", () => {
    const noDesc = validatePayForm({ amountSat: "10", description: "   ", reference: "" });
    expect(noDesc.ok).toBe(false);
    if (!noDesc.ok) expect(noDesc.errors.description).toMatch(/140/);
    const longDesc = validatePayForm({ amountSat: "10", description: "x".repeat(141), reference: "" });
    expect(longDesc.ok).toBe(false);
    const longRef = validatePayForm({ amountSat: "10", description: "ok", reference: "r".repeat(65) });
    expect(longRef.ok).toBe(false);
    if (!longRef.ok) expect(longRef.errors.reference).toMatch(/64/);
    expect(validatePayForm({ amountSat: "10", description: "x".repeat(140), reference: "r".repeat(64) }).ok).toBe(true);
  });
});

describe("KAI PAY — QR-Payload, Beträge, Countdown", () => {
  it("kodiert die Lightning-URI in GROSSBUCHSTABEN (alphanumerischer QR-Modus)", () => {
    expect(qrPayload(" lightning:lnbc50u1pj9x2yzpp5abc ")).toBe("LIGHTNING:LNBC50U1PJ9X2YZPP5ABC");
  });

  it("formatiert sat mit Tausendertrennung, nie mit EUR", () => {
    expect(formatSats(5000)).toBe("5.000 sat");
    expect(formatSats(1)).toBe("1 sat");
    expect(formatSats(null)).toBe("—");
  });

  it("Countdown: Restsekunden nie negativ, mm:ss unter einer Stunde", () => {
    const now = Date.parse("2026-09-08T10:00:00Z");
    expect(remainingSeconds("2026-09-08T10:09:32Z", now)).toBe(572);
    expect(remainingSeconds("2026-09-08T09:59:00Z", now)).toBe(0);
    expect(remainingSeconds("kein datum", now)).toBeNull();
    expect(remainingSeconds(null, now)).toBeNull();
    expect(formatCountdown(572)).toBe("09:32");
    expect(formatCountdown(0)).toBe("00:00");
    expect(formatCountdown(3661)).toBe("1:01:01");
    expect(formatCountdown(null)).toBe("—");
  });
});

describe("KAI PAY — Antworten normalisieren", () => {
  const row = {
    payment_id: "p1",
    status: "WAITING",
    amount_sat: 10,
    paid_amount_sat: null,
    paid_at: null,
    reference: null,
    description: "t",
    created_at: "2026-09-08T10:00:00Z",
    expires_at: "2026-09-08T10:10:00Z",
    last_error: null,
  };

  it("nimmt Array oder {items|requests} und wirft Müll weg", () => {
    expect(normalizePayList([row])).toEqual([row]);
    expect(normalizePayList({ items: [row, { nope: true }] })).toEqual([row]);
    expect(normalizePayList({ requests: [row] })).toEqual([row]);
    expect(normalizePayList("garbage")).toEqual([]);
    expect(normalizePayList(null)).toEqual([]);
  });

  it("POST-Antwort → Ansichtsobjekt in GET-Form", () => {
    const v = toPayRequest({
      payment_id: "p9",
      status: "WAITING",
      amount_sat: 5000,
      description: "Test",
      reference: null,
      bolt11: "lnbc...",
      lightning_uri: "lightning:lnbc...",
      created_at: "2026-09-08T10:00:00Z",
      expires_at: "2026-09-08T10:10:00Z",
    });
    expect(v).toEqual({
      payment_id: "p9",
      status: "WAITING",
      amount_sat: 5000,
      paid_amount_sat: null,
      paid_at: null,
      reference: null,
      description: "Test",
      created_at: "2026-09-08T10:00:00Z",
      expires_at: "2026-09-08T10:10:00Z",
      last_error: null,
    });
  });

  it("Fehler werden lesbar: HTTP-Status · Art · Detail", () => {
    expect(describeError(new ApiError("server", 503, "/pay/requests", "journal locked"))).toBe(
      "HTTP 503 · server · journal locked",
    );
    expect(describeError(new ApiError("network", 0, "/pay/requests", "Failed to fetch"))).toBe(
      "keine Verbindung · network · Failed to fetch",
    );
    expect(describeError(new Error("boom"))).toBe("boom");
    expect(describeError(undefined)).toBe("unbekannter Fehler");
  });
});
