import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

// qrcode gemockt: kein echtes Rendering im Test, aber wir prüfen Payload + Bild.
const toDataURL = vi.fn();
vi.mock("qrcode", () => ({
  default: { toDataURL: (...args: unknown[]) => toDataURL(...args) },
}));

const fetchPayHealth = vi.fn();
const createPayRequest = vi.fn();
const fetchPayRequest = vi.fn();
const fetchPayRequests = vi.fn();
const fetchPayReceipt = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    fetchPayHealth: (s?: AbortSignal) => fetchPayHealth(s),
    createPayRequest: (b: unknown, s?: AbortSignal) => createPayRequest(b, s),
    fetchPayRequest: (id: string, s?: AbortSignal) => fetchPayRequest(id, s),
    fetchPayRequests: (limit: number, s?: AbortSignal) => fetchPayRequests(limit, s),
    fetchPayReceipt: (id: string, s?: AbortSignal) => fetchPayReceipt(id, s),
  };
});

import { ApiError, type PayReceipt, type PayRequest, type PayRequestCreated } from "@/lib/api";
import { PayPage } from "./Pay";

const health = {
  enabled: true,
  open_requests: 1,
  settled_total: 7,
  last_settled_at: "2026-09-08T09:00:00Z",
  poller_alive: true,
};

const created: PayRequestCreated = {
  payment_id: "pay_abc123",
  status: "WAITING",
  amount_sat: 5000,
  description: "Test",
  reference: null,
  bolt11: "lnbc50u1pj9x2yzpp5abcdefexample",
  lightning_uri: "lightning:lnbc50u1pj9x2yzpp5abcdefexample",
  created_at: "2026-09-08T10:00:00Z",
  expires_at: "2099-01-01T00:00:00Z",
};

const waiting: PayRequest = {
  payment_id: "pay_abc123",
  status: "WAITING",
  amount_sat: 5000,
  paid_amount_sat: null,
  paid_at: null,
  reference: null,
  description: "Test",
  created_at: "2026-09-08T10:00:00Z",
  expires_at: "2099-01-01T00:00:00Z",
  last_error: null,
};

const settled: PayRequest = {
  ...waiting,
  status: "SETTLED",
  paid_amount_sat: 5000,
  paid_at: "2026-09-08T10:01:30Z",
};

const receipt: PayReceipt = {
  receipt_id: "rcpt_777",
  payment_id: "pay_abc123",
  amount_sat: 5000,
  paid_amount_sat: 5000,
  paid_at: "2026-09-08T10:01:30Z",
  reference: null,
  description: "Test",
  rail: "lightning",
  audit: { journal_seq: 42, record_hash: "deadbeefcafe" },
  created_at: "2026-09-08T10:01:31Z",
};

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

function fillAndSubmit(amount = "5000", description = "Test") {
  fireEvent.change(screen.getByLabelText(/Amount \(sat\)/), { target: { value: amount } });
  fireEvent.change(screen.getByLabelText(/^Description/), { target: { value: description } });
  fireEvent.click(screen.getByRole("button", { name: "CREATE PAYMENT" }));
}

beforeEach(() => {
  vi.clearAllMocks();
  fetchPayHealth.mockResolvedValue(health);
  fetchPayRequests.mockResolvedValue([]);
  toDataURL.mockResolvedValue("data:image/png;base64,QR");
});
afterEach(cleanup);

describe("PayPage — deaktiviert", () => {
  it("404 auf /pay/health → Hinweisseite mit APP_PAY_ENABLED, kein Formular", async () => {
    fetchPayHealth.mockRejectedValue(new ApiError("not_found", 404, "/pay/health", "kai pay disabled"));
    fetchPayRequests.mockRejectedValue(new ApiError("not_found", 404, "/pay/requests", "kai pay disabled"));
    render(<PayPage pollMs={20} />);
    expect(await screen.findByText(/nicht aktiviert/)).toBeTruthy();
    expect(screen.getByText("KAI PAY ist auf diesem Server nicht aktiviert (APP_PAY_ENABLED)")).toBeTruthy();
    expect(screen.getByText(/kai pay disabled/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "CREATE PAYMENT" })).toBeNull();
  });

  it("enabled=false in der Health-Antwort zählt genauso als deaktiviert", async () => {
    fetchPayHealth.mockResolvedValue({ ...health, enabled: false });
    render(<PayPage pollMs={20} />);
    expect(await screen.findByText(/nicht aktiviert/)).toBeTruthy();
  });

  it("anderer Health-Fehler (500) blockiert das Formular NICHT, steht aber lesbar da", async () => {
    fetchPayHealth.mockRejectedValue(new ApiError("server", 500, "/pay/health", "poller crashed"));
    render(<PayPage pollMs={20} />);
    expect(await screen.findByText(/poller crashed/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "CREATE PAYMENT" })).toBeTruthy();
  });
});

describe("PayPage — Zahlung anfordern", () => {
  it("CREATE PAYMENT → QR aus lightning_uri in GROSSBUCHSTABEN, bolt11 kopierbar, Status: WAITING", async () => {
    createPayRequest.mockResolvedValue(created);
    fetchPayRequest.mockResolvedValue(waiting);
    render(<PayPage pollMs={1_000_000} />);
    await screen.findByText(/offen 1 · settled 7/);

    fillAndSubmit();

    const img = (await screen.findByTestId("pay-qr")) as HTMLImageElement;
    expect(img.getAttribute("src")).toBe("data:image/png;base64,QR");
    expect(toDataURL).toHaveBeenCalledTimes(1);
    expect(toDataURL.mock.calls[0][0]).toBe("LIGHTNING:LNBC50U1PJ9X2YZPP5ABCDEFEXAMPLE");

    expect(createPayRequest.mock.calls[0][0]).toEqual({ amount_sat: 5000, description: "Test" });
    expect((screen.getByLabelText("bolt11") as HTMLInputElement).value).toBe(created.bolt11);
    expect(screen.getByRole("button", { name: "bolt11 kopieren" })).toBeTruthy();
    expect(screen.getByText("Status: WAITING")).toBeTruthy();
    expect(screen.getByText(/Läuft ab in/)).toBeTruthy();
    expect(screen.getByText("5.000 sat")).toBeTruthy();
  });

  it("Validierung: '5.00' ist kein sat-Betrag — kein Request abgeschickt", async () => {
    render(<PayPage pollMs={20} />);
    await screen.findByRole("button", { name: "CREATE PAYMENT" });
    fillAndSubmit("5.00");
    expect(await screen.findByText(/ganze Zahl/)).toBeTruthy();
    expect(createPayRequest).not.toHaveBeenCalled();
  });

  it("API-Fehler beim Erstellen steht lesbar auf der Seite", async () => {
    createPayRequest.mockRejectedValue(new ApiError("server", 503, "/pay/requests", "journal locked"));
    render(<PayPage pollMs={20} />);
    await screen.findByRole("button", { name: "CREATE PAYMENT" });
    fillAndSubmit();
    expect(await screen.findByText(/HTTP 503 · server · journal locked/)).toBeTruthy();
    expect(screen.queryByTestId("pay-qr")).toBeNull();
  });
});

describe("PayPage — Status-Polling bis Endzustand", () => {
  it("WAITING → SETTLED: grüne Zeile, Polling stoppt, Receipt lädt", async () => {
    createPayRequest.mockResolvedValue(created);
    fetchPayRequest.mockResolvedValueOnce(waiting).mockResolvedValue(settled);
    fetchPayReceipt.mockResolvedValue(receipt);
    render(<PayPage pollMs={20} />);
    await screen.findByText("Noch keine Zahlungsanforderungen.");
    fillAndSubmit();

    const line = await screen.findByText("✓ PAYMENT SETTLED");
    expect(line.className).toContain("text-pos");
    expect(screen.getByText(/5\.000 sat · /)).toBeTruthy();

    const calls = fetchPayRequest.mock.calls.length;
    expect(calls).toBe(2);
    await sleep(120);
    expect(fetchPayRequest).toHaveBeenCalledTimes(calls);
    expect(screen.queryByText(/Status wird alle/)).toBeNull();
    // Liste wurde neu geladen: initial + nach Create + nach SETTLED.
    await waitFor(() => expect(fetchPayRequests).toHaveBeenCalledTimes(3));

    fireEvent.click(screen.getByRole("button", { name: /Receipt/ }));
    expect(await screen.findByText("rcpt_777")).toBeTruthy();
    expect(screen.getByText("deadbeefcafe")).toBeTruthy();
    expect(screen.getByText("lightning")).toBeTruthy();
    expect(fetchPayReceipt.mock.calls[0][0]).toBe("pay_abc123");
  });

  it("EXPIRED ist klar rot", async () => {
    createPayRequest.mockResolvedValue(created);
    fetchPayRequest.mockResolvedValue({ ...waiting, status: "EXPIRED" });
    render(<PayPage pollMs={20} />);
    await screen.findByRole("button", { name: "CREATE PAYMENT" });
    fillAndSubmit();
    const line = await screen.findByText("✗ PAYMENT EXPIRED");
    expect(line.className).toContain("text-neg");
    await sleep(80);
    expect(fetchPayRequest).toHaveBeenCalledTimes(1);
  });

  it("FAILED ist rot und zeigt last_error", async () => {
    createPayRequest.mockResolvedValue(created);
    fetchPayRequest.mockResolvedValue({ ...waiting, status: "FAILED", last_error: "no route to destination" });
    render(<PayPage pollMs={20} />);
    await screen.findByRole("button", { name: "CREATE PAYMENT" });
    fillAndSubmit();
    const line = await screen.findByText("✗ PAYMENT FAILED");
    expect(line.className).toContain("text-neg");
    expect(screen.getByText("no route to destination")).toBeTruthy();
  });

  it("Fehler der Statusabfrage steht auf der Seite, Seed bleibt sichtbar", async () => {
    createPayRequest.mockResolvedValue(created);
    fetchPayRequest.mockRejectedValue(new ApiError("server", 502, "/pay/requests/pay_abc123", "upstream down"));
    render(<PayPage pollMs={1_000_000} />);
    await screen.findByRole("button", { name: "CREATE PAYMENT" });
    fillAndSubmit();
    expect(await screen.findByText(/upstream down/)).toBeTruthy();
    expect(screen.getByText("Status: WAITING")).toBeTruthy();
  });
});

describe("PayPage — Liste der letzten Requests", () => {
  it("zeigt Badge/Betrag/Referenz und lädt einen Request per Klick (ohne QR — Vertrag liefert kein bolt11)", async () => {
    const older: PayRequest = {
      ...settled,
      payment_id: "pay_old1",
      amount_sat: 21000,
      reference: "ref-A",
      description: "Alt",
    };
    fetchPayRequests.mockResolvedValue([older, { ...waiting, payment_id: "pay_w2", reference: "ref-B" }]);
    fetchPayRequest.mockResolvedValue({ ...waiting, payment_id: "pay_w2", reference: "ref-B" });
    render(<PayPage pollMs={1_000_000} />);

    expect(await screen.findByText("ref-A")).toBeTruthy();
    expect(screen.getByText("ref-B")).toBeTruthy();
    expect(screen.getByText("21.000 sat")).toBeTruthy();
    expect(screen.getAllByText("SETTLED").length).toBe(1);

    fireEvent.click(screen.getByRole("button", { name: "Request pay_old1 laden" }));
    expect(await screen.findByText("✓ PAYMENT SETTLED")).toBeTruthy();
    expect(screen.getByText(/nur direkt nach dem Erstellen/)).toBeTruthy();
    expect(screen.queryByTestId("pay-qr")).toBeNull();
    // terminaler Listeneintrag → kein Polling
    await sleep(40);
    expect(fetchPayRequest).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Request pay_w2 laden" }));
    expect(await screen.findByText("Status: WAITING")).toBeTruthy();
    await waitFor(() => expect(fetchPayRequest).toHaveBeenCalledTimes(1));
    expect(fetchPayRequest.mock.calls[0][0]).toBe("pay_w2");
  });

  it("Listen-Fehler steht lesbar da, Formular bleibt nutzbar", async () => {
    fetchPayRequests.mockRejectedValue(new ApiError("server", 500, "/pay/requests", "index corrupt"));
    render(<PayPage pollMs={20} />);
    expect(await screen.findByText(/index corrupt/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "CREATE PAYMENT" })).toBeTruthy();
  });
});
