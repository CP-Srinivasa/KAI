import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";

const fetchMomentumUniverse = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    fetchMomentumUniverse: (s?: AbortSignal) => fetchMomentumUniverse(s),
  };
});

import { MomentumUniversePanel } from "./MomentumUniversePanel";

const snapshot = {
  available: true,
  ts: "2026-06-26T10:00:00Z",
  count: 2,
  universe: [
    { symbol: "BTC/USDT", rank: 1, universe_score: 0.91, volume_score: 0.88, momentum_score: 0.95 },
    { symbol: "ETH/USDT", rank: 2, universe_score: 0.4, volume_score: 0.2, momentum_score: 0.55 },
  ],
};

beforeEach(() => vi.clearAllMocks());
afterEach(cleanup);

describe("MomentumUniversePanel", () => {
  it("zeigt das gerankte Universe mit Volumen-/Momentum-Percentilen", async () => {
    fetchMomentumUniverse.mockResolvedValue(snapshot);
    const { container } = render(<MomentumUniversePanel />);
    await screen.findByText("BTC/USDT");
    const text = container.textContent ?? "";
    expect(text).toContain("ETH/USDT");
    expect(text).toContain("2 Coins");
    expect(text).toContain("V 88%"); // Volumen-Percentile (most traded)
    expect(text).toContain("M 95%"); // Momentum-Percentile (best performer)
  });

  it("ist fail-closed: leer → ehrliche 'noch kein Snapshot'", async () => {
    fetchMomentumUniverse.mockResolvedValue({ available: false, reason: "no_snapshot" });
    render(<MomentumUniversePanel />);
    expect(await screen.findByText(/Noch kein Universe-Snapshot/)).toBeTruthy();
  });

  it("Info erklärt die eigene Datenbasis (kein TradingView-Scraping)", async () => {
    fetchMomentumUniverse.mockResolvedValue(snapshot);
    render(<MomentumUniversePanel />);
    await screen.findByText("BTC/USDT");
    fireEvent.click(screen.getByRole("button", { name: /Was bedeutet das/ }));
    expect(await screen.findByText(/kein\s+TradingView-Scraping/)).toBeTruthy();
  });
});
