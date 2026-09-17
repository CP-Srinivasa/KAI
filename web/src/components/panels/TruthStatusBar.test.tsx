import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { TruthStatusBar } from "./TruthStatusBar";
import type { DashboardQuality } from "@/lib/api";

describe("TruthStatusBar", () => {
  afterEach(cleanup);

  it("zeigt die Report-Zeit nicht ein zweites Mal in der Diagnose (steht nur in der Lage-Leiste)", () => {
    const quality = { generated_at: "2026-09-17T09:00:00Z" } as unknown as DashboardQuality;
    render(
      <TruthStatusBar quality={quality} regime={null} priorityGate={null} qualityState="ready" />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Warum unverändert/ }));
    expect(screen.getByText("Build-Hash")).toBeTruthy();
    expect(screen.queryByText("Report-Stand")).toBeNull();
    expect(screen.queryByText(/2026-09-17 09:00:00/)).toBeNull();
  });
});
