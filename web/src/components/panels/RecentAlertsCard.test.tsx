import { describe, it, expect, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";

import { RecentAlertsCard } from "./RecentAlertsCard";
import type { DashboardQuality } from "@/lib/api";

type Row = DashboardQuality["recent_alerts"][number];

function row(overrides: Partial<Row>): Row {
  return {
    doc_id: "doc",
    sentiment: "bullish",
    priority: null,
    assets: ["BTC"],
    dispatched_at: "2026-09-17T07:00",
    outcome: "",
    source_name: null,
    priority_basis: "unknown",
    ...overrides,
  };
}

function renderCard(rows: Row[]) {
  const data = { recent_alerts: rows } as unknown as DashboardQuality;
  return render(<RecentAlertsCard data={data} state="ready" generatedAt={null} />);
}

afterEach(cleanup);

describe("RecentAlertsCard – Prioritaet (Audit P0-4)", () => {
  it("zeigt fuer Webhook-Zeilen 'Webhook' statt eines stummen Strichs", () => {
    renderCard([
      row({ doc_id: "tv:tvsig_1", source_name: "tradingview_webhook", priority_basis: "webhook" }),
    ]);
    const badge = screen.getByText("Webhook");
    expect(badge.closest("[title]")?.getAttribute("title")).toMatch(/keinen Analysewert/);
  });

  it("zeigt Analysewerte weiter mit Band", () => {
    renderCard([row({ doc_id: "news-1", priority: 9, priority_basis: "analysis" })]);
    expect(screen.getByText("9")).toBeTruthy();
    expect(screen.getByText("kritisch")).toBeTruthy();
  });

  it("bleibt bei unbekannter Herkunft beim Strich und behauptet keinen Webhook", () => {
    renderCard([row({ doc_id: "old-1", assets: [], priority_basis: "unknown" })]);
    expect(screen.queryByText("Webhook")).toBeNull();
    expect(screen.getByTitle("Keine Prioritaet im Datensatz")).toBeTruthy();
  });

  it("alte Vertraege ohne priority_basis zeigen den Strich", () => {
    const legacy = row({ doc_id: "legacy" });
    delete (legacy as Partial<Row>).priority_basis;
    renderCard([legacy]);
    expect(screen.getByTitle("Keine Prioritaet im Datensatz")).toBeTruthy();
  });
});
