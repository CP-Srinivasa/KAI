// Operator-Befund 2026-07-30: das Panel meldete „Kuratierter Snapshot · Stand
// 2026-07-12 · 18 Tage alt — veraltet, bitte pflegen" auf einer Datei, die nur
// ABGESCHLOSSENE Phasen enthielt. Diese Tests halten beide Hälften des Fixes
// fest: die LIVE-Sektion (offene Prä-Regs, pflegefrei) und die korrigierte
// Chronik-Semantik (erledigt ≠ veraltet).
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";

const fetchOperatorBoard = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    fetchOperatorBoard: (s?: AbortSignal) => fetchOperatorBoard(s),
  };
});

import { AcutePointsBoard } from "./AcutePointsBoard";

const prereg = (over: Record<string, unknown> = {}) => ({
  prereg_id: "b20ef1487ccba99d",
  name: "directional_news_hedged_1d_drift",
  state: "maturing",
  n_proxy: 247,
  n_target: 300,
  progress_pct: 82.3,
  per_source: {},
  action: "reift (247/300) — kein Attest vor Ziel-n.",
  sample_size_target: 300,
  created_at_utc: "2026-07-02T00:00:00Z",
  last_verdict: null,
  ...over,
});

const board = (over: Record<string, unknown> = {}) => ({
  stand: "2026-07-12",
  note: "",
  todos: [],
  phases: [{ label: "Dashboard UI-Update", status: "done" }],
  improvements: [],
  generated_at: "2026-07-30T10:00:00Z",
  age_days: 18,
  is_stale: false,
  curated_has_open_items: false,
  content_type: "curated_chronicle",
  live: {
    open_preregs: [prereg()],
    open_count: 1,
    due_count: 0,
    has_content: true,
    maturity_state: "ok",
    note: "Live aus prereg_ledger − prereg_verdicts (ADR 0012).",
    generated_at: new Date().toISOString(),
  },
  ...over,
});

const props = {
  quality: null,
  regime: null,
  priorityGate: null,
  qualityState: "ready" as const,
};

beforeEach(() => vi.clearAllMocks());
afterEach(cleanup);

describe("AcutePointsBoard — Live-Sektion", () => {
  it("zeigt offene Prä-Regs mit Reife-Fortschritt statt Handpflege", async () => {
    fetchOperatorBoard.mockResolvedValue(board());
    const { container } = render(<AcutePointsBoard {...props} />);
    await screen.findByText(/directional_news_hedged_1d_drift/);
    const text = container.textContent ?? "";
    expect(text).toContain("Offene Prä-Regs");
    expect(text).toContain("1 offen");
    expect(text).toContain("n≈247/300");
    expect(text).toContain("reift");
    expect(text).toContain("live berechnet");
  });

  it("ein fälliges Verdikt darf NIE als 'ruhig' erscheinen", async () => {
    // Keine akuten Gates (quality=null) — früher hätte das Panel „ruhig"
    // gemeldet, obwohl ein pre-registriertes Verdikt fällig ist.
    fetchOperatorBoard.mockResolvedValue(
      board({
        live: {
          ...board().live,
          open_preregs: [prereg({ state: "due", n_proxy: 300, progress_pct: 100 })],
          due_count: 1,
        },
      }),
    );
    const { container } = render(<AcutePointsBoard {...props} />);
    await screen.findByText(/Verdikt fällig/);
    const text = container.textContent ?? "";
    expect(text).toContain("1 Verdikt fällig");
    expect(text).not.toContain("ruhig");
  });

  it("markiert ungezählte Claims ehrlich, ohne Zahl zu erfinden", async () => {
    fetchOperatorBoard.mockResolvedValue(
      board({
        live: {
          ...board().live,
          open_preregs: [
            prereg({ state: "no_counter", n_proxy: null, n_target: null, progress_pct: null }),
          ],
        },
      }),
    );
    const { container } = render(<AcutePointsBoard {...props} />);
    await screen.findAllByText(/ungezählt/);
    expect(container.textContent ?? "").not.toContain("n≈");
  });

  it("meldet fehlende Reife-Zähler statt stillschweigend zu urteilen", async () => {
    fetchOperatorBoard.mockResolvedValue(
      board({ live: { ...board().live, maturity_state: "unavailable" } }),
    );
    render(<AcutePointsBoard {...props} />);
    expect(await screen.findByText(/Reife-Zähler nicht erreichbar/)).toBeTruthy();
  });
});

describe("AcutePointsBoard — Chronik-Semantik", () => {
  it("erledigte Phasen erzeugen KEIN 'veraltet, bitte pflegen'", async () => {
    fetchOperatorBoard.mockResolvedValue(board());
    const { container } = render(<AcutePointsBoard {...props} />);
    await screen.findByText(/Dashboard UI-Update/);
    const text = container.textContent ?? "";
    expect(text).not.toContain("bitte pflegen");
    expect(text).not.toContain("Tage alt");
    expect(text).toContain("veraltet nicht");
  });

  it("ein OFFENER kuratierter Punkt wird weiterhin angemahnt", async () => {
    fetchOperatorBoard.mockResolvedValue(
      board({
        todos: [{ text: "irgendwas Offenes" }],
        is_stale: true,
        curated_has_open_items: true,
      }),
    );
    const { container } = render(<AcutePointsBoard {...props} />);
    await screen.findByText(/irgendwas Offenes/);
    expect(container.textContent ?? "").toContain("bitte pflegen");
  });
});
