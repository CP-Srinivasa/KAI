import { describe, it, expect, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { PreparedPanel } from "./PreparedPanel";

afterEach(cleanup);

describe("PreparedPanel honest status badge", () => {
  it("defaults to Roadmap (no misleading percent/phase)", () => {
    render(<PreparedPanel title="X" />);
    expect(screen.getByText("Roadmap")).toBeInTheDocument();
    // No leftover maturity-percent text.
    expect(screen.queryByText(/%/)).not.toBeInTheDocument();
  });

  it("renders Live-only", () => {
    render(<PreparedPanel title="Kapital" status="live_only" />);
    expect(screen.getByText("Live-only")).toBeInTheDocument();
  });

  it("renders Backend nicht erreichbar for unavailable", () => {
    render(<PreparedPanel title="X" status="unavailable" />);
    expect(screen.getByText("Backend nicht erreichbar")).toBeInTheDocument();
  });

  it("renders Paper-Mode and Keine Daten", () => {
    const { rerender } = render(<PreparedPanel title="X" status="paper_only" />);
    expect(screen.getByText("Paper-Mode")).toBeInTheDocument();
    rerender(<PreparedPanel title="X" status="no_data" />);
    expect(screen.getByText("Keine Daten")).toBeInTheDocument();
  });

  it("ignores legacy phase/progress (no percent rendered) but keeps roadmapNote", () => {
    render(
      <PreparedPanel
        title="X"
        phase="planning"
        progress={42}
        roadmapNote="Roadmap: GET /operator/foo."
      />,
    );
    expect(screen.queryByText(/42/)).not.toBeInTheDocument();
    expect(screen.getByText("Roadmap: GET /operator/foo.")).toBeInTheDocument();
  });
});
