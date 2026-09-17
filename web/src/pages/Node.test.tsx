import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

// SP-8 / T6: Lightning-Karte und LN-Steuerung lasen denselben Endpunkt mit je
// einem eigenen Poller — zwei Abrufe von /dashboard/api/lightning pro Runde.
const fetchLightningStatus = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    fetchLightningStatus: (s?: AbortSignal) => fetchLightningStatus(s),
  };
});

// Die uebrigen Karten der Seite haben eigene Endpunkte und sind hier nicht Gegenstand.
vi.mock("@/components/panels/ChainPanel", () => ({ ChainPanel: () => null }));
vi.mock("@/components/panels/ChannelsPanel", () => ({ ChannelsPanel: () => null }));
vi.mock("@/components/panels/NodeReputationPanel", () => ({ NodeReputationPanel: () => null }));
vi.mock("@/components/panels/BlitzInfoPanel", () => ({ BlitzInfoPanel: () => null }));
vi.mock("@/components/panels/AuditIntegrityKpi", () => ({ AuditIntegrityKpi: () => null }));

import { NodePage } from "./Node";

describe("NodePage", () => {
  afterEach(() => {
    cleanup();
    fetchLightningStatus.mockReset();
  });

  it("ruft /dashboard/api/lightning genau einmal ab und speist beide Karten daraus", async () => {
    fetchLightningStatus.mockResolvedValue({
      state: "disabled",
      pay_enabled: false,
      generated_at: "2026-09-17T09:00:00Z",
    });
    render(<NodePage />);

    // Beide Karten zeigen denselben Stand: die LN-Steuerung meldet den Kill-Switch.
    await waitFor(() => expect(screen.getByText(/Kill-Switch AN/)).toBeTruthy());
    expect(fetchLightningStatus).toHaveBeenCalledTimes(1);
  });
});
