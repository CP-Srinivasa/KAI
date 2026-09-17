import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

vi.mock("@/lib/useBackendHealth", () => ({
  useBackendHealth: () => ({ state: "connected", version: "0.42", detail: null }),
}));
vi.mock("@/state/PortfolioSnapshotProvider", () => ({
  useSharedPortfolioSnapshot: () => ({ state: "loading", data: null, error: null, reload: () => {} }),
}));

import { CommandHeader } from "./CommandHeader";
import type { KaiRuntimeState } from "@/kai/types";

function renderHeader(kai: KaiRuntimeState | null) {
  return render(
    <CommandHeader
      kai={kai}
      quality={null}
      regime={null}
      priorityGate={null}
      qualityState="loading"
    />,
  );
}

describe("CommandHeader", () => {
  afterEach(cleanup);

  it("zeigt den Backend-Zustand in der Lage-Leiste", () => {
    renderHeader(null);
    expect(screen.getByText("Backend verbunden")).toBeTruthy();
  });

  it("rendert fuer den Phase-1-Stub keine Pille (SP-8 / T1: Stub-Pill entfaellt)", () => {
    renderHeader({ state: "IDLE", is_stub: true, phase: 1 } as unknown as KaiRuntimeState);
    expect(screen.queryByText(/Stub/)).toBeNull();
    expect(screen.queryByText(/Live · IDLE/)).toBeNull();
  });
});
