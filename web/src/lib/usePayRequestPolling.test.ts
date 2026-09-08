import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";

const fetchPayRequest = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    fetchPayRequest: (id: string, s?: AbortSignal) => fetchPayRequest(id, s),
  };
});

import { ApiError, type PayRequest } from "@/lib/api";
import { usePayRequestPolling } from "./usePayRequestPolling";

const base: PayRequest = {
  payment_id: "p1",
  status: "WAITING",
  amount_sat: 5000,
  paid_amount_sat: null,
  paid_at: null,
  reference: null,
  description: "Test",
  created_at: "2026-09-08T10:00:00Z",
  expires_at: "2026-09-08T10:10:00Z",
  last_error: null,
};
const settled: PayRequest = { ...base, status: "SETTLED", paid_amount_sat: 5000, paid_at: "2026-09-08T10:01:00Z" };

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

beforeEach(() => vi.clearAllMocks());
afterEach(() => vi.restoreAllMocks());

describe("usePayRequestPolling", () => {
  it("pollt bis SETTLED und hört dann auf — kein weiterer Aufruf danach", async () => {
    fetchPayRequest
      .mockResolvedValueOnce(base)
      .mockResolvedValueOnce(base)
      .mockResolvedValue(settled);
    const onTerminal = vi.fn();
    const { result } = renderHook(() => usePayRequestPolling("p1", null, 20, onTerminal));

    await waitFor(() => expect(result.current.request?.status).toBe("SETTLED"), { timeout: 2000 });
    expect(result.current.polling).toBe(false);
    expect(result.current.error).toBeNull();
    const callsAtTerminal = fetchPayRequest.mock.calls.length;
    expect(callsAtTerminal).toBe(3);

    await sleep(120); // > 5 Intervalle — dürfte nichts mehr auslösen
    expect(fetchPayRequest).toHaveBeenCalledTimes(callsAtTerminal);
    expect(onTerminal).toHaveBeenCalledTimes(1);
    expect(onTerminal).toHaveBeenCalledWith(settled);
  });

  it("hört bei EXPIRED und FAILED genauso auf", async () => {
    for (const status of ["EXPIRED", "FAILED"] as const) {
      fetchPayRequest.mockReset();
      fetchPayRequest.mockResolvedValue({ ...base, status });
      const { result, unmount } = renderHook(() => usePayRequestPolling("p1", null, 20));
      await waitFor(() => expect(result.current.request?.status).toBe(status));
      await sleep(80);
      expect(fetchPayRequest).toHaveBeenCalledTimes(1);
      expect(result.current.polling).toBe(false);
      unmount();
    }
  });

  it("ein bereits terminaler Seed (aus der Liste) wird gar nicht erst gepollt", async () => {
    const { result } = renderHook(() => usePayRequestPolling("p1", settled, 20));
    await sleep(80);
    expect(fetchPayRequest).not.toHaveBeenCalled();
    expect(result.current.request).toEqual(settled);
    expect(result.current.polling).toBe(false);
  });

  it("404 (unbekannter Request) stoppt das Polling und zeigt den Fehler", async () => {
    fetchPayRequest.mockRejectedValue(new ApiError("not_found", 404, "/pay/requests/p1", "unknown payment"));
    const { result } = renderHook(() => usePayRequestPolling("p1", null, 20));
    await waitFor(() => expect(result.current.error).toMatch(/not_found/));
    expect(result.current.error).toMatch(/unknown payment/);
    await sleep(80);
    expect(fetchPayRequest).toHaveBeenCalledTimes(1);
    expect(result.current.polling).toBe(false);
  });

  it("Netzfehler hält das Polling am Leben: Fehler sichtbar, dann Erfolg löscht ihn", async () => {
    // Zweite Antwort wird von Hand freigegeben, damit das Fehlerfenster
    // beobachtbar bleibt (sonst ist es bei 20 ms Intervall schon vorbei).
    let release!: (v: PayRequest) => void;
    const gate = new Promise<PayRequest>((r) => {
      release = r;
    });
    fetchPayRequest
      .mockRejectedValueOnce(new ApiError("network", 0, "/pay/requests/p1", "Failed to fetch"))
      .mockImplementationOnce(() => gate)
      .mockResolvedValue(settled);
    const { result } = renderHook(() => usePayRequestPolling("p1", base, 20));
    await waitFor(() => expect(result.current.error ?? "").toMatch(/Failed to fetch/));
    // Seed bleibt sichtbar, während der Fehler steht — kein leeres Panel.
    expect(result.current.request).toEqual(base);
    expect(result.current.polling).toBe(true);
    release(base);
    await waitFor(() => expect(result.current.request?.status).toBe("SETTLED"), { timeout: 2000 });
    expect(result.current.error).toBeNull();
    expect(result.current.polling).toBe(false);
  });

  it("ohne paymentId gibt es nichts zu pollen", async () => {
    const { result } = renderHook(() => usePayRequestPolling(null, null, 20));
    await sleep(60);
    expect(fetchPayRequest).not.toHaveBeenCalled();
    expect(result.current).toEqual({ request: null, error: null, polling: false });
  });
});
