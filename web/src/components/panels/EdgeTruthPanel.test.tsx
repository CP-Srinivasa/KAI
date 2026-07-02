import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";

// Mock only the network fetcher; keep ApiError + types from the real module.
const fetchEdgeVerdict = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    fetchEdgeVerdict: (c: boolean, s?: AbortSignal) => fetchEdgeVerdict(c, s),
  };
});

import { EdgeTruthPanel } from "./EdgeTruthPanel";

// Reale Lage 2026-06-25: Gate n>=30 ERREICHT (n=51) → Edge belastbar WIDERLEGT
// (P=16,5%), und ausreißer-robust (ohne besten Trade fällt P auf 4,0%).
const canonicalVerdict = {
  available: true,
  canonical: true,
  contaminated: false,
  source_allowlist: ["autonomous_generator", "real_analysis"],
  closes_excluded_by_source: 157,
  trade_count: 51,
  p_mu_net_positive: 0.1648,
  median_net_bps: -85.7,
  mean_net_bps: -24.4,
  realized_pnl_usd_sum: -11.31,
  quarantine_excluded_count: 14,
  live_orders_attempted: 0,
  window_started_at: "2026-03-22T20:04:53Z",
  window_ended_at: "2026-06-25T16:38:40Z",
  edge_gate_n: 30,
  gate_reached: true,
  verdict: "disproven" as const,
  without_best_p: 0.0402,
  without_best_mean_bps: -37.3,
  bootstrap_ci_95: [-68.6, 24.7] as [number, number],
  // Kosten-Wahrheit: Brutto-Edge auch ~0/negativ → Break-even-Kosten < Maker-
  // Untergrenze → cost_reachable=false = SIGNAL-Problem, nicht Kostenproblem.
  p_mu_gross_positive: 0.3,
  gross_mean_bps: -4.4,
  gross_median_bps: -65.7,
  breakeven_roundtrip_bps: -4.4,
  current_cost_roundtrip_bps: 22.3,
  maker_floor_roundtrip_bps: 4.0,
  cost_reachable: false,
  error: null,
};

beforeEach(() => vi.clearAllMocks());
afterEach(cleanup);

describe("EdgeTruthPanel", () => {
  it("zeigt bei erreichtem Gate (n>=30, P<50%) 'belastbar widerlegt' + Gate-Badge + Ausreißer-Test", async () => {
    fetchEdgeVerdict.mockResolvedValue(canonicalVerdict);
    const { container } = render(<EdgeTruthPanel />);

    await screen.findByText(/Edge belastbar widerlegt/);

    const text = container.textContent ?? "";
    expect(text).toContain("16,5 %"); // P(mu_net>0) de-DE
    expect(text).toContain("belastbar widerlegt"); // Klartext: Negativ-Befund, nicht "zu dünn"
    expect(text).toContain("0,9 %"); // Median -85,7 bps -> ~0,9 % verständlich
    expect(text).toContain("Stichproben-Gate n≥30 erreicht"); // Gate sichtbar
    expect(text).toContain("Ausreißer-Test"); // Robustheit sichtbar
    expect(text).toContain("4,0 %"); // without_best_p 0.0402 -> nicht ausreißer-getragen
    // Kosten-Wahrheit: cost_reachable=false → Signalproblem, Execution-Alpha-Illusion benannt.
    expect(text).toContain("Kosten-Wahrheit");
    expect(text).toContain("Signalproblem");
    expect(text).toContain("Execution-Alpha kann das nicht retten");
    expect(text).not.toContain("Kostenproblem möglich"); // nicht der reachable-Zweig
    expect(text).toContain("Canonical");
    expect(text).toContain("157 Close");
    expect(text).toContain("14 korrupte Close");
    expect(text).not.toContain("kontaminiert"); // canonical = NICHT kontaminiert
    expect(text).not.toContain("reine Beweislage"); // Info-Feld eingeklappt
  });

  it("bei tragfähigem Brutto-Edge (cost_reachable) zeigt es 'Kostenproblem möglich', nicht Signalproblem", async () => {
    fetchEdgeVerdict.mockResolvedValue({
      ...canonicalVerdict,
      p_mu_gross_positive: 0.97,
      gross_mean_bps: 18.0,
      gross_median_bps: 12.0,
      breakeven_roundtrip_bps: 18.0,
      cost_reachable: true,
    });
    const { container } = render(<EdgeTruthPanel />);
    await screen.findByText(/Edge belastbar widerlegt/);
    const text = container.textContent ?? "";
    expect(text).toContain("Kostenproblem möglich");
    expect(text).not.toContain("Signalproblem");
    expect(text).not.toContain("Execution-Alpha kann das nicht retten");
  });

  it("unter dem Gate (n<30) sagt es 'Stichprobe zu klein', nicht widerlegt", async () => {
    fetchEdgeVerdict.mockResolvedValue({
      ...canonicalVerdict,
      trade_count: 20,
      gate_reached: false,
      verdict: "insufficient" as const,
      p_mu_net_positive: 0.3,
    });
    const { container } = render(<EdgeTruthPanel />);
    await screen.findByText(/Stichprobe zu klein/);
    const text = container.textContent ?? "";
    expect(text).toContain("n=20");
    expect(text).toContain("noch nicht erreicht"); // Gate-Badge im Negativ-Zustand
    expect(text).not.toContain("belastbar widerlegt");
  });

  it("Info-Feld ist standardmäßig eingeklappt und öffnet die Nachlese-Erklärung auf Klick", async () => {
    fetchEdgeVerdict.mockResolvedValue(canonicalVerdict);
    render(<EdgeTruthPanel />);
    await screen.findByText(/Edge belastbar widerlegt/);

    expect(screen.queryByText(/reine Beweislage/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Was bedeutet das/ }));
    expect(await screen.findByText(/reine Beweislage/)).toBeTruthy();
    expect(screen.getByText(/über alles entscheidet/)).toBeTruthy();
  });

  it("warnt im vollen Stream, dass er kontaminiert ist", async () => {
    fetchEdgeVerdict.mockResolvedValue({
      ...canonicalVerdict,
      canonical: false,
      contaminated: true,
      source_allowlist: null,
      closes_excluded_by_source: 0,
      trade_count: 147,
    });
    const { container } = render(<EdgeTruthPanel />);
    await screen.findByText(/kontaminiert/i);
    expect(container.textContent ?? "").toContain("Mai-Canary");
  });

  it("ist fail-closed: zeigt ehrliche Unverfügbarkeit statt Absturz", async () => {
    fetchEdgeVerdict.mockResolvedValue({
      available: false,
      canonical: true,
      error: "audit_file_missing",
    });
    render(<EdgeTruthPanel />);
    expect(await screen.findByText(/nicht verfügbar/i)).toBeTruthy();
  });
});
