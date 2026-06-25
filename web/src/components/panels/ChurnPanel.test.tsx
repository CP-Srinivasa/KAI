import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";

const fetchChurnReport = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    fetchChurnReport: (s?: AbortSignal) => fetchChurnReport(s),
  };
});

import { ChurnPanel } from "./ChurnPanel";
import { CurrencyProvider } from "@/state/CurrencyProvider";

function renderPanel() {
  return render(
    <CurrencyProvider>
      <ChurnPanel />
    </CurrencyProvider>,
  );
}

// Reale Prod-Form (saubere Epoche): Brutto leicht positiv, Fees drehen es ins Minus.
const report = {
  available: true,
  since: "2026-06-11",
  window_start: "2026-06-11",
  window_end: "2026-06-25",
  trading_days: 15,
  realization_count: 116,
  final_close_count: 98,
  partial_count: 18,
  excluded_count: 0,
  gross_usd: 153.0,
  open_fees_usd: 477.66,
  close_fees_usd: 476.86,
  round_trip_fees_usd: 954.52,
  net_usd: -801.52,
  fee_drag_pct: 623.9,
  gross_near_zero: false,
  trades_per_trading_day: 7.7,
  fee_spend_per_trading_day: 66.15,
  per_day: [
    { date: "2026-06-24", fills: 8, realizations: 4, fee_spend_usd: 40.0, realized_gross_usd: 10 },
    { date: "2026-06-25", fills: 12, realizations: 6, fee_spend_usd: 80.0, realized_gross_usd: -5 },
  ],
  hold_minutes_median: 147,
  hold_minutes_p25: 52,
  hold_minutes_p75: 491,
  hold_under_15min_pct: 9,
  hold_under_1h_pct: 28,
  by_reason: [
    { reason: "stop", count: 66, net_usd: -4559.89, winrate: 0 },
    { reason: "take", count: 29, net_usd: 3192.9, winrate: 1 },
    { reason: "tp_tier", count: 18, net_usd: 573.89, winrate: 1 },
  ],
  note: "READ-ONLY",
  error: null,
};

beforeEach(() => vi.clearAllMocks());
afterEach(cleanup);

describe("ChurnPanel", () => {
  it("zeigt Brutto→Netto ehrlich: Fees kehren ein Plus in einen Verlust um", async () => {
    fetchChurnReport.mockResolvedValue(report);
    const { container } = renderPanel();
    await screen.findByText(/Vor Kosten/);
    const text = container.textContent ?? "";
    expect(text).toContain("kehren ein knappes Plus in einen Verlust"); // Kernsatz
    expect(text).toContain("6.2×"); // Fee-Drag als Vielfaches der Brutto-Bewegung
    expect(text).toContain("116 Realisierungen"); // Partials mitgezählt (S-001)
    expect(text).toContain("partielle 18");
    expect(text).toContain("stop"); // Exit-Grund-Split
  });

  it("Info-Feld eingeklappt, öffnet die Erklärung inkl. Paper-Caveat auf Klick", async () => {
    fetchChurnReport.mockResolvedValue(report);
    renderPanel();
    await screen.findByText(/Vor Kosten/);
    expect(screen.queryByText(/Fees simuliert/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Was bedeutet das/ }));
    expect(await screen.findByText(/Fees simuliert/)).toBeTruthy();
    expect(screen.getByText(/Mindesthaltedauer wäre hier kontraproduktiv/)).toBeTruthy();
  });

  it("Brutto≈0: Fee-Drag wird als instabil markiert statt eine Riesenzahl zu zeigen", async () => {
    fetchChurnReport.mockResolvedValue({
      ...report,
      gross_usd: 0.2,
      gross_near_zero: true,
      fee_drag_pct: null,
    });
    const { container } = renderPanel();
    await screen.findByText(/Vor Kosten/);
    expect(container.textContent ?? "").toContain("Brutto ≈ 0");
  });

  it("ist fail-closed: zeigt ehrliche Unverfügbarkeit statt Absturz", async () => {
    fetchChurnReport.mockResolvedValue({ available: false, error: "audit_file_missing" });
    renderPanel();
    expect(await screen.findByText(/nicht verfügbar/i)).toBeTruthy();
  });
});
