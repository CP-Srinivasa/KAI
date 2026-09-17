import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { BackendStatus } from "@/lib/useBackendHealth";

// SP-8 Teil 2: Die Topbar-Pille ersetzt den BackendStatusBanner. Inventar des
// Banners (Stand d27e708d) — jede Zeile muss ueber die Pille erreichbar bleiben:
//   checking     -> "Backend wird geprüft …"
//   connected    -> "Backend verbunden · v<version>"  (Version wandert in den Footer)
//   unauthorized -> "Backend erreichbar, aber Auth fehlgeschlagen — Seite neu laden"
//   offline      -> "Backend offline · <detail>"
//   role=status / aria-live=polite
let current: BackendStatus = { state: "checking", version: null, detail: null };
vi.mock("@/lib/useBackendHealth", () => ({
  useBackendHealth: () => current,
}));

import { BackendStatusPill } from "./BackendStatusPill";

function renderWith(s: BackendStatus) {
  current = s;
  return render(<BackendStatusPill />);
}

describe("BackendStatusPill", () => {
  afterEach(cleanup);

  it("checking -> Pille 'Backend prüft …', Detail 'Backend wird geprüft …'", () => {
    renderWith({ state: "checking", version: null, detail: null });
    const btn = screen.getByRole("button", { name: /Backend/ });
    expect(btn.textContent).toContain("Backend prüft …");
    expect(btn.getAttribute("title")).toContain("Backend wird geprüft …");
    fireEvent.click(btn);
    expect(screen.getByRole("dialog").textContent).toContain("Backend wird geprüft …");
  });

  it("connected -> kompakt 'Backend ok', Detail 'Backend verbunden', keine Version (die steht im Footer)", () => {
    renderWith({ state: "connected", version: "0.42.1", detail: null });
    const btn = screen.getByRole("button", { name: /Backend/ });
    expect(btn.textContent).toContain("Backend ok");
    fireEvent.click(btn);
    const dialog = screen.getByRole("dialog");
    expect(dialog.textContent).toContain("Backend verbunden");
    expect(document.body.textContent).not.toContain("0.42.1");
  });

  it("unauthorized -> Pille 'Backend: Anmeldung', Detail mit Ursache und 'Seite neu laden'", () => {
    renderWith({ state: "unauthorized", version: null, detail: "HTTP 401 unauthorized" });
    const btn = screen.getByRole("button", { name: /Backend/ });
    expect(btn.textContent).toContain("Backend: Anmeldung");
    expect(btn.getAttribute("title")).toContain("Auth fehlgeschlagen — Seite neu laden");
    fireEvent.click(btn);
    const dialog = screen.getByRole("dialog");
    expect(dialog.textContent).toContain("Backend erreichbar, aber Auth fehlgeschlagen — Seite neu laden");
    expect(dialog.textContent).toContain("HTTP 401 unauthorized");
  });

  it("offline -> Pille 'Backend offline', Detail mit der Ursache", () => {
    renderWith({ state: "offline", version: null, detail: "Failed to fetch" });
    const btn = screen.getByRole("button", { name: /Backend/ });
    expect(btn.textContent).toContain("Backend offline");
    expect(btn.getAttribute("title")).toContain("Backend offline · Failed to fetch");
    fireEvent.click(btn);
    expect(screen.getByRole("dialog").textContent).toContain("Backend offline · Failed to fetch");
  });

  it("ist tastaturbedienbar: aria-expanded, Escape schliesst, Live-Region meldet den Zustand", () => {
    renderWith({ state: "offline", version: null, detail: "Failed to fetch" });
    const btn = screen.getByRole("button", { name: /Backend/ });
    expect(btn.getAttribute("aria-expanded")).toBe("false");
    expect(btn.getAttribute("aria-haspopup")).toBe("dialog");
    fireEvent.click(btn);
    expect(btn.getAttribute("aria-expanded")).toBe("true");
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByRole("status").textContent).toContain("Backend offline · Failed to fetch");
  });
});
