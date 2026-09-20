import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const lnValueAction = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, lnValueAction: (request: unknown) => lnValueAction(request) };
});
vi.mock("@/components/panels/PayQr", () => ({
  PayQr: ({ lightningUri }: { lightningUri: string }) => <div data-testid="invoice-qr">{lightningUri}</div>,
}));

import { LnControlPanel } from "./LnControlPanel";
import type { LightningStatus } from "@/lib/api";

const status = {
  state: "ready" as const,
  data: { pay_enabled: true, generated_at: "2026-09-19T10:00:00Z" } as LightningStatus,
  error: null,
  reload: () => {},
  fetchedAt: Date.now(),
};

describe("LnControlPanel", () => {
  afterEach(() => {
    cleanup();
    lnValueAction.mockReset();
    vi.unstubAllGlobals();
  });

  it("zeigt für den Payment-Control-Plane-Plan HOTP, bindet die Freigabe und hält den Schlüssel beim Retry", async () => {
    lnValueAction
      .mockResolvedValueOnce({
        mode: "plan",
        action: "pay_invoice",
        policy: { decision: "payment_control_plane", reason: "ADR 0018 §12" },
        plan_hash: "abc123",
        plan: { route: "payment_control_plane", mode: "live", amount_sat: 1000, fee_limit_sat: 5 },
      })
      .mockRejectedValueOnce(new Error("Netzwerk unterbrochen"))
      .mockResolvedValueOnce({ mode: "execute", action: "pay_invoice", result: { status: "SETTLED" } });

    render(<LnControlPanel status={status} />);
    fireEvent.change(screen.getByLabelText("Aktion"), { target: { value: "pay_invoice" } });
    fireEvent.change(screen.getByLabelText("Lightning-Rechnung"), { target: { value: "lnbc1invoice" } });
    fireEvent.click(screen.getByRole("button", { name: "Plan" }));
    await waitFor(() => expect(screen.getByLabelText("HOTP-Freigabe")).toBeTruthy());
    fireEvent.change(screen.getByLabelText("HOTP-Freigabe"), { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: "Ausführen" }));
    await waitFor(() => expect(screen.getByText("Netzwerk unterbrochen")).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "Ausführen" }));
    await waitFor(() => expect(screen.getByText(/SETTLED/)).toBeTruthy());

    expect(lnValueAction.mock.calls[0][0]).toMatchObject({
      action: "pay_invoice",
      params: { payment_request: "lnbc1invoice", purpose: "operator_pay_invoice" },
    });
    const first = lnValueAction.mock.calls[1][0];
    expect(first.confirm).toMatchObject({ hotp: "123456", plan_hash: "abc123" });
    expect(first.confirm.idempotency_key).toBeTruthy();
    expect(lnValueAction.mock.calls[2][0].confirm.idempotency_key).toBe(first.confirm.idempotency_key);
  });

  it("entwertet die Vorschau bei geänderter Rechnung und bietet Rechnungs-Erstellung ohne JSON an", async () => {
    lnValueAction.mockResolvedValue({
      mode: "plan",
      action: "pay_invoice",
      policy: { decision: "payment_control_plane", reason: "ADR 0018 §12" },
      plan_hash: "abc123",
      plan: { route: "payment_control_plane", mode: "live", amount_sat: 1000, fee_limit_sat: 5 },
    });
    render(<LnControlPanel status={status} />);
    fireEvent.change(screen.getByLabelText("Aktion"), { target: { value: "pay_invoice" } });
    fireEvent.change(screen.getByLabelText("Lightning-Rechnung"), { target: { value: "lnbc1invoice" } });
    fireEvent.click(screen.getByRole("button", { name: "Plan" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Ausführen" })).toBeTruthy());
    fireEvent.change(screen.getByLabelText("Lightning-Rechnung"), { target: { value: "lnbc1different" } });
    expect(screen.queryByRole("button", { name: "Ausführen" })).toBeNull();

    fireEvent.change(screen.getByLabelText("Aktion"), { target: { value: "create_invoice" } });
    expect(screen.getByLabelText("Betrag in sat")).toBeTruthy();
    expect(screen.getByLabelText("Memo")).toBeTruthy();
    expect(screen.queryByRole("textbox", { name: /JSON/i })).toBeNull();
  });

  it("erstellt eine Rechnung über das Empfangs-Gate auch bei gesperrtem Sendepfad", async () => {
    lnValueAction
      .mockResolvedValueOnce({
        mode: "plan",
        action: "create_invoice",
        policy: { decision: "receive_gate", reason: "capital-free mint" },
        plan_hash: "invoice-plan",
        plan: { action: "create_invoice", state: "planned" },
      })
      .mockResolvedValueOnce({
        mode: "execute",
        action: "create_invoice",
        result: { action: "create_invoice", state: "executed", response: { payment_request: "lnbc1return" } },
      });
    render(<LnControlPanel status={{ ...status, data: { ...status.data, pay_enabled: false } }} />);
    fireEvent.change(screen.getByLabelText("Aktion"), { target: { value: "create_invoice" } });
    fireEvent.change(screen.getByLabelText("Betrag in sat"), { target: { value: "321" } });
    fireEvent.change(screen.getByLabelText("Memo"), { target: { value: "Beleg" } });
    fireEvent.click(screen.getByRole("button", { name: "Plan" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Ausführen" })).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "Ausführen" }));
    await waitFor(() => expect(screen.getByText(/executed/)).toBeTruthy());
    expect((screen.getByLabelText("Erstellte Lightning-Rechnung") as HTMLTextAreaElement).value).toBe("lnbc1return");
    expect(screen.getByRole("button", { name: "Rechnung kopieren" })).toBeTruthy();
    expect(screen.getByTestId("invoice-qr").textContent).toBe("lightning:lnbc1return");
    const originalClipboard = Object.getOwnPropertyDescriptor(navigator, "clipboard");
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    try {
      fireEvent.click(screen.getByRole("button", { name: "Rechnung kopieren" }));
      await waitFor(() => expect(writeText).toHaveBeenCalledWith("lnbc1return"));
    } finally {
      if (originalClipboard) Object.defineProperty(navigator, "clipboard", originalClipboard);
      else Reflect.deleteProperty(navigator, "clipboard");
    }
    expect(lnValueAction.mock.calls[0][0]).toMatchObject({
      action: "create_invoice",
      params: { value_sat: 321, memo: "Beleg" },
    });
    expect(lnValueAction.mock.calls[1][0].confirm).toMatchObject({ plan_hash: "invoice-plan", hotp: "" });
    expect(lnValueAction.mock.calls[1][0].confirm.idempotency_key).toBeTruthy();
  });

  it.each([
    { payEnabled: false, mode: "live" },
    { payEnabled: true, mode: "shadow" },
  ])("zeigt Senden bei pay_enabled=$payEnabled und Modus=$mode, sperrt aber Ausführen", async ({ payEnabled, mode }) => {
    lnValueAction.mockResolvedValue({
      mode: "plan",
      action: "pay_invoice",
      policy: { decision: "payment_control_plane", reason: "ADR 0018 §12" },
      plan_hash: "plan",
      plan: { mode, amount_sat: 1, fee_limit_sat: 1 },
    });
    render(<LnControlPanel status={{ ...status, data: { ...status.data, pay_enabled: payEnabled } }} />);
    expect(screen.getByRole("option", { name: "Senden · Rechnung bezahlen" })).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Lightning-Rechnung"), { target: { value: "lnbc1invoice" } });
    fireEvent.click(screen.getByRole("button", { name: "Plan" }));
    await waitFor(() => expect(screen.getByText(/Vorschau ist keine Zahlungsfreigabe/)).toBeTruthy());
    expect(screen.queryByRole("button", { name: "Ausführen" })).toBeNull();
    expect(lnValueAction).toHaveBeenCalledTimes(1);
  });
});
