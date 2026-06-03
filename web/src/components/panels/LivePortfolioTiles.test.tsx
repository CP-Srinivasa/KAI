import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";

// Mock only the network fetchers; keep ApiError + types from the real module so
// useApi's `instanceof ApiError` check keeps working.
const fetchPortfolioSnapshot = vi.fn();
const fetchExposureSummary = vi.fn();
const fetchRecentCycles = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    fetchPortfolioSnapshot: (s?: AbortSignal) => fetchPortfolioSnapshot(s),
    fetchExposureSummary: (s?: AbortSignal) => fetchExposureSummary(s),
    fetchRecentCycles: (n?: number, s?: AbortSignal) => fetchRecentCycles(n, s),
  };
});

import { LivePortfolioTiles } from "./LivePortfolioTiles";

const flatPortfolio = {
  report_type: "paper_portfolio_snapshot",
  generated_at: "2026-06-03T20:00:00Z",
  source: "paper",
  audit_path: "x",
  cash_usd: 100000,
  realized_pnl_usd: 0,
  total_market_value_usd: 0,
  total_equity_usd: 100000,
  position_count: 0,
  positions: [],
};

const flatExposure = {
  report_type: "paper_exposure_summary",
  priced_position_count: 0,
  stale_position_count: 0,
  unavailable_price_count: 0,
  gross_exposure_usd: 0,
  net_exposure_usd: 0,
  largest_position_symbol: null,
  largest_position_weight_pct: null,
  mark_to_market_status: "ok",
  execution_enabled: false,
  write_back_allowed: false,
  generated_at: "2026-06-03T20:00:00Z",
  available: true,
  error: null,
};

beforeEach(() => {
  vi.clearAllMocks();
});
afterEach(cleanup);

describe("LivePortfolioTiles", () => {
  it("renders real equity + honest flat-book empty states", async () => {
    fetchPortfolioSnapshot.mockResolvedValue(flatPortfolio);
    fetchExposureSummary.mockResolvedValue(flatExposure);
    fetchRecentCycles.mockResolvedValue({
      report_type: "recent_cycles",
      total_cycles: 0,
      status_counts: {},
      recent_cycles: [],
    });

    render(<LivePortfolioTiles />);

    // Portfolio tile reached the ready state (labels are deterministic; the exact
    // currency grouping is Intl/env-dependent so we assert the $-value loosely).
    expect((await screen.findAllByText("Equity")).length).toBeGreaterThanOrEqual(1);
    expect((await screen.findAllByText(/\$100[,.]?000/)).length).toBeGreaterThanOrEqual(1);
    expect((await screen.findAllByText(/Kein Markteinsatz/)).length).toBeGreaterThanOrEqual(1);
    expect(
      (await screen.findAllByText(/Keine bewertbaren Positionen/)).length,
    ).toBeGreaterThanOrEqual(1);
    expect((await screen.findAllByText(/Keine Cycles im Fenster/)).length).toBeGreaterThanOrEqual(
      1,
    );
  });

  it("renders honest 'nicht erreichbar' on backend error", async () => {
    fetchPortfolioSnapshot.mockRejectedValue(new Error("boom"));
    fetchExposureSummary.mockRejectedValue(new Error("boom"));
    fetchRecentCycles.mockRejectedValue(new Error("boom"));

    render(<LivePortfolioTiles />);

    const errs = await screen.findAllByText(/nicht erreichbar/);
    expect(errs.length).toBeGreaterThanOrEqual(1);
  });
});
