import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";

const fetchMomentumCrosscheck = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    fetchMomentumCrosscheck: (s?: AbortSignal) => fetchMomentumCrosscheck(s),
  };
});

import { MomentumCrosscheckPanel } from "./MomentumCrosscheckPanel";

const snapshot = {
  available: true,
  ts: "2026-06-26T15:00:00Z",
  count: 3,
  rows: [
    { symbol: "LAB/USDT", rank: 1, momentum_score: 0.94, ta_label: "strong_buy", ta_score: 0.87, ta_trend: "up", rsi: 68, agreement: "agree_bullish" },
    { symbol: "BTC/USDT", rank: 2, momentum_score: 0.59, ta_label: "strong_sell", ta_score: -0.98, ta_trend: "down", rsi: 28, agreement: "divergence" },
    { symbol: "SLX/USDT", rank: 3, momentum_score: 0.98, ta_label: "unavailable", ta_score: null, ta_trend: "unavailable", rsi: null, agreement: "unavailable" },
  ],
};

beforeEach(() => vi.clearAllMocks());
afterEach(cleanup);

describe("MomentumCrosscheckPanel", () => {
  it("zeigt Übereinstimmung und Divergenz pro Coin", async () => {
    fetchMomentumCrosscheck.mockResolvedValue(snapshot);
    const { container } = render(<MomentumCrosscheckPanel />);
    await screen.findByText("LAB/USDT");
    const text = container.textContent ?? "";
    expect(text).toContain("BTC/USDT");
    expect(text).toContain("Einig bullish"); // LAB agree_bullish
    expect(text).toContain("Divergenz"); // BTC divergence
    expect(text).toContain("1 Divergenz"); // genau eine Divergenz
  });

  it("ist fail-closed: leer → ehrliche 'noch kein Snapshot'", async () => {
    fetchMomentumCrosscheck.mockResolvedValue({ available: false, reason: "no_snapshot" });
    render(<MomentumCrosscheckPanel />);
    expect(await screen.findByText(/Noch kein Cross-Check-Snapshot/)).toBeTruthy();
  });

  it("Info erklärt den ToS-konformen TA-Ersatz (kein Scraping)", async () => {
    fetchMomentumCrosscheck.mockResolvedValue(snapshot);
    render(<MomentumCrosscheckPanel />);
    await screen.findByText("LAB/USDT");
    fireEvent.click(screen.getByRole("button", { name: /Was bedeutet das/ }));
    expect(await screen.findByText(/kein Scraping/)).toBeTruthy();
  });
});
