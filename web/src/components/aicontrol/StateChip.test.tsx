import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { StateChip } from "./StateChip";

describe("StateChip", () => {
  it("zeigt Symbol, Label und Grund", () => {
    render(<StateChip state="gestoert" reason="Proxy nicht erreichbar" />);
    expect(screen.getByText("GESTÖRT")).toBeTruthy();
    expect(screen.getByTitle("Proxy nicht erreichbar")).toBeTruthy();
  });

  it("unbekannt statt leer", () => {
    render(<StateChip state={null} reason="KI-Transport-Status nicht lesbar" />);
    expect(screen.getByText("UNBEKANNT")).toBeTruthy();
  });
});
