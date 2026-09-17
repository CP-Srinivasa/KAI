import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import type { BackendStatus } from "@/lib/useBackendHealth";

let current: BackendStatus = { state: "checking", version: null, detail: null };
vi.mock("@/lib/useBackendHealth", () => ({
  useBackendHealth: () => current,
}));

import { AppFooter } from "./AppFooter";

describe("AppFooter", () => {
  afterEach(cleanup);

  it("zeigt die Backend-Version, sobald verbunden", () => {
    current = { state: "connected", version: "0.42.1", detail: null };
    render(<AppFooter />);
    expect(screen.getByRole("contentinfo").textContent).toContain("Backend v0.42.1");
  });

  it("behauptet ohne Verbindung keine Version", () => {
    current = { state: "offline", version: null, detail: "Failed to fetch" };
    render(<AppFooter />);
    expect(screen.getByRole("contentinfo").textContent).toContain("Backend-Version n/v");
  });
});
