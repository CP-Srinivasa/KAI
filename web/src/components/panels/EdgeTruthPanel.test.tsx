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

const canonicalVerdict = {
  available: true,
  canonical: true,
  contaminated: false,
  source_allowlist: ["autonomous_generator", "real_analysis"],
  closes_excluded_by_source: 119,
  trade_count: 28,
  p_mu_net_positive: 0.2488,
  median_net_bps: -77.7,
  mean_net_bps: -21.0,
  realized_pnl_usd_sum: -103.7,
  quarantine_excluded_count: 14,
  live_orders_attempted: 0,
  window_started_at: "2026-06-11T00:00:00Z",
  window_ended_at: "2026-06-23T12:00:00Z",
  error: null,
};

beforeEach(() => vi.clearAllMocks());
afterEach(cleanup);

describe("EdgeTruthPanel", () => {
  it("zeigt das canonical-Verdikt EHRLICH (kein bewiesener Edge bei P<50%) + Quellen-/Quarantäne-Transparenz", async () => {
    fetchEdgeVerdict.mockResolvedValue(canonicalVerdict);
    const { container } = render(<EdgeTruthPanel />);

    // erscheint erst im ready-state -> wartet das Laden ab
    await screen.findByText(/Kein bewiesener Edge/);

    const text = container.textContent ?? "";
    expect(text).toContain("24,9 %"); // P(mu_net>0) ehrlich gerundet (de-DE Komma)
    expect(text).toContain("Verdient KAI nach Kosten Geld"); // Klartext-Satz für Nicht-Quants
    expect(text).toContain("0,8 %"); // Median -77,7 bps -> ~0,8 % verständlich übersetzt
    expect(text).toContain("Canonical"); // source-filter-status sichtbar
    expect(text).toContain("119 Close"); // closes_excluded_by_source nachvollziehbar
    expect(text).toContain("14 korrupte Close"); // quarantine-transparenz
    expect(text).not.toContain("kontaminiert"); // canonical = NICHT kontaminiert
    expect(text).not.toContain("reine Beweislage"); // Info-Feld standardmäßig eingeklappt
  });

  it("Info-Feld ist standardmäßig eingeklappt und öffnet die Nachlese-Erklärung auf Klick", async () => {
    fetchEdgeVerdict.mockResolvedValue(canonicalVerdict);
    render(<EdgeTruthPanel />);
    await screen.findByText(/Kein bewiesener Edge/);

    // eingeklappt -> Erklärtext nicht im DOM
    expect(screen.queryByText(/reine Beweislage/)).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /Was bedeutet das/ }));

    // aufgeklappt -> Erklärung lesbar
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
