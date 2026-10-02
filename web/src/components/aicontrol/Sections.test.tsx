import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { AiControlResponse } from "@/lib/aiControl";

import { Accounts, AttentionList, Connections, HeaderStrip, History, Protocol, Workloads } from "./Sections";

const LEER: AiControlResponse = {
  schema: "ai-control/v1",
  generated_at: "2026-10-02T12:00:00+00:00",
  summary: {
    today_usd: 0,
    today_limit_usd: null,
    month_usd: 0,
    month_limit_usd: null,
    projected_month_usd: null,
    budget_state: "OK",
    budget_exhausted_at: null,
    budget_end_estimate: null,
    calls_today: 0,
    tokens_in_today: 0,
    tokens_out_today: 0,
    state_counts: {},
  },
  attention: [],
  connections: {
    proxy: { state: null, reason: null, since: null, version: null, lock_matches: null },
    aliases: [],
    providers: [],
  },
  workloads: [
    {
      purpose: "analysis",
      title: "Analyse",
      route: "standard",
      mode: "off",
      state: "bereit",
      reason: "noch kein Aufruf",
      since: null,
      calls_today: 0,
      tokens_in_today: 0,
      tokens_out_today: 0,
      approx_kb_today: 0,
      cost_today_usd: 0,
      unknown_cost_calls_today: 0,
      failure_rate_24h: null,
      fallbacks_today: 0,
      sparfenster: "enforce",
      parts: [],
    },
  ],
  accounts: [
    {
      provider: "openai",
      status: "kein_api",
      balance: null,
      currency: null,
      error: null,
      fetched_at: "2026-10-02T12:00:00+00:00",
      topup_url: "https://platform.openai.com",
      runway_days: null,
      stale: false,
      detail: {},
    },
  ],
  accounts_written_at: null,
  protocol: [],
  null_reasons: { "connections.proxy.state": "KI-Transport-Status nicht lesbar" },
};

describe("KI-Kontrolle Bereiche", () => {
  it("rendert einen leeren Vertrag ohne Absturz und ohne erfundene Zahlen", () => {
    render(
      <>
        <HeaderStrip s={LEER.summary} />
        <AttentionList items={LEER.attention} />
        <Connections c={LEER.connections} reasons={LEER.null_reasons} />
        <Workloads items={LEER.workloads} />
        <Accounts items={LEER.accounts} writtenAt={LEER.accounts_written_at} />
        <Protocol items={LEER.protocol} />
        <History h={{ schema: "ai-control-history/v1", days: [] }} />
      </>,
    );
    expect(screen.getByText("nichts offen", { exact: false })).toBeTruthy();
    expect(screen.getByText("nur KAI-Messung")).toBeTruthy();
    expect(screen.getByText("UNBEKANNT")).toBeTruthy();
    expect(screen.getByText("Sparfenster enforce", { exact: false })).toBeTruthy();
  });

  it("zeigt fehlende Telemetrie als keine Daten mit Grund statt 0,00 $ (Review I1)", () => {
    const grund = "llm_telemetry.jsonl fehlt -- Verbrauch und Aufgaben unbekannt";
    const ohne: AiControlResponse["summary"] = {
      ...LEER.summary,
      today_usd: null,
      month_usd: null,
      calls_today: null,
      tokens_in_today: null,
      tokens_out_today: null,
    };
    render(
      <>
        <HeaderStrip s={ohne} />
        <Workloads items={[]} reason={grund} />
      </>,
    );
    expect(screen.queryByText("0,00 $", { exact: false })).toBeNull();
    expect(screen.getByText("Verbrauch unbekannt", { exact: false })).toBeTruthy();
    expect(screen.getByText(`keine Daten (${grund})`)).toBeTruthy();
  });

  it("zeigt OpenAI-Monatskosten als Monat ohne Reichweite (Review I2)", () => {
    const openai = { ...LEER.accounts[0], status: "ok" as const, currency: "USD" };
    render(
      <Accounts
        items={[{ ...openai, detail: { kind: "month_cost", month_cost_usd: 4.1 } }]}
        writtenAt="2026-10-02T12:00:00+00:00"
      />,
    );
    expect(screen.getByText("Monat 4,10 $")).toBeTruthy();
    expect(screen.queryByText("Reichweite", { exact: false })).toBeNull();
  });
});
