import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";

import { AITransportPanel } from "./AITransportPanel";
import type { AiTransportResponse } from "@/lib/api";
import type { AsyncState } from "@/lib/useApi";
import { formatDayTime } from "@/lib/time";

afterEach(cleanup);

const COMMIT = "ca1a188c0123456789abcdef0123456789abcdef";

// Alle sechs Katalogrouten, gemischte Modi und Stati: ein offener und ein
// halboffener Circuit, veraltete Evidenz, Nulls mit Gruenden, Lock-Abweichung.
function fixture(): AiTransportResponse {
  return {
    schema_version: "ai-transport/v1",
    generated_at: "2026-09-30T16:30:00+00:00",
    transport: {
      proxy_alive: true,
      proxy_status_code: 200,
      version: "1.102.1",
      tree: "/home/ubuntu/transport/litellm/1.102.1-8fe49f6f",
      manifest: "8fe49f6fd0352080",
      verified_at: null,
      lock_matches: false,
      tree_spec_sha256: "4d9c64c0".padEnd(64, "a"),
      lock_sha256: "7a1b2c3d".padEnd(64, "b"),
    },
    runtime: {
      runtime_commit: COMMIT,
      runtime_source: "release",
      enabled: true,
      mode_ceiling: "advisory",
      shadow_grace_seconds: 1.0,
      detached_shadows: 2,
      max_detached_shadows: 8,
    },
    routes: [
      {
        route: "bulk",
        mode: "off",
        alias: "kai-bulk",
        timeout_seconds: 30,
        deadline_seconds: 127.3,
        circuit: [],
        report: { status: "KEINE_EVIDENZ", missing: [], transports: {} },
      },
      {
        route: "standard",
        mode: "shadow",
        alias: "kai-standard",
        timeout_seconds: 30,
        deadline_seconds: 127.3,
        circuit: [{ upstream: null, state: "open", consecutive_failures: 5, probe_in_flight: false }],
        report: {
          status: "LUECKENHAFT",
          missing: ["litellm:evidenzalter", "direct:identitaet"],
          transports: {
            litellm: {
              status: "LUECKENHAFT",
              calls: 2,
              last_success_at: "2026-09-10T14:59:00+00:00",
              identity_share: 1.0,
              cost_sum_known_usd: 0.010505,
              cost_unknown: 0,
              failures: 0,
              fallback_rate: 0.0,
              max_retry_count: 0,
              newest_age_hours: 480.1,
              stale: true,
              release_sha: null,
            },
            direct: {
              status: "LUECKENHAFT",
              calls: 5,
              last_success_at: null,
              identity_share: null,
              cost_sum_known_usd: null,
              cost_unknown: 5,
              failures: 5,
              fallback_rate: null,
              max_retry_count: null,
              newest_age_hours: 1.5,
              stale: false,
              release_sha: null,
            },
          },
        },
      },
      {
        route: "reasoning",
        mode: "shadow",
        alias: "kai-reasoning",
        timeout_seconds: 60,
        deadline_seconds: 250,
        circuit: [{ upstream: "openai/gpt-5", state: "closed", consecutive_failures: 0, probe_in_flight: false }],
        report: {
          status: "BELEGT",
          missing: [],
          transports: {
            litellm: {
              status: "BELEGT",
              calls: 12,
              last_success_at: "2026-09-30T15:40:00+00:00",
              identity_share: 0.955,
              cost_sum_known_usd: 0.2,
              cost_unknown: 0,
              failures: 0,
              fallback_rate: 0.0,
              max_retry_count: 1,
              newest_age_hours: 0.8,
              stale: false,
              release_sha: COMMIT,
            },
          },
        },
      },
      {
        route: "critical",
        mode: "primary",
        alias: "kai-critical",
        timeout_seconds: 20,
        deadline_seconds: 90,
        circuit: [
          { upstream: "anthropic/claude", state: "half_open", consecutive_failures: 3, probe_in_flight: true },
        ],
        report: null,
      },
      {
        route: "stt",
        mode: "off",
        alias: "kai-stt",
        timeout_seconds: 45,
        deadline_seconds: 100,
        circuit: [],
        report: { status: "KEINE_EVIDENZ", missing: [] },
      },
      {
        route: "research",
        mode: "advisory",
        alias: "kai-research",
        timeout_seconds: 180,
        deadline_seconds: 400,
        circuit: [],
        report: {
          status: "BELEGT",
          missing: [],
          transports: {
            litellm: {
              status: "BELEGT",
              calls: 1,
              last_success_at: "2026-09-30T12:00:00+00:00",
              identity_share: 1,
              cost_sum_known_usd: 0.05,
              cost_unknown: 0,
              failures: 0,
              fallback_rate: 0,
              max_retry_count: 0,
              newest_age_hours: 4.5,
              stale: false,
              release_sha: COMMIT,
            },
          },
        },
      },
    ],
    report: {
      available: true,
      generated_at: "2026-09-30T16:00:05+00:00",
      age_hours: 0.5,
      status_counts: { BELEGT: 2, LUECKENHAFT: 1, KEINE_EVIDENZ: 2 },
    },
    null_reasons: {
      "transport.verified_at": "Zeile traegt keinen Zeitstempel",
      "routes.critical.report": "Route steht nicht im Routenbericht",
      "routes.standard.direct.last_success_at": "kein erfolgreicher Direktaufruf im Fenster",
      "routes.standard.direct.identity_share": "keine Antwort mit belegter Identitaet",
      "routes.standard.litellm.release_sha": "keine Telemetriezeile traegt runtime_commit",
    },
  };
}

function ready(data: AiTransportResponse): AsyncState<AiTransportResponse> {
  return { state: "ready", data, error: null, reload: vi.fn(), fetchedAt: Date.now() };
}

function failed(kind: string, status: number, message = "Not Found"): AsyncState<AiTransportResponse> {
  return { state: "error", data: null, error: { kind, message, status }, reload: vi.fn() };
}

function row(label: string): HTMLElement {
  return screen.getByRole("listitem", { name: `Route ${label}` });
}

describe("AITransportPanel", () => {
  it("zeigt alle sechs Routen in Katalogreihenfolge mit Klartextnamen", () => {
    render(<AITransportPanel state={ready(fixture())} />);
    expect(screen.getByText("KI-Transport (LiteLLM)")).toBeInTheDocument();
    const items = within(screen.getByRole("list", { name: "Routen" })).getAllByRole("listitem");
    expect(items.map((li) => li.getAttribute("aria-label"))).toEqual([
      "Route Masse",
      "Route Standard",
      "Route Abwägung",
      "Route Kritisch",
      "Route Sprache zu Text",
      "Route Recherche",
    ]);
  });

  it("faerbt Modus-Badges nach Vertrag", () => {
    render(<AITransportPanel state={ready(fixture())} />);
    expect(within(row("Masse")).getByText("aus").className).toContain("bg-bg-2");
    expect(within(row("Standard")).getByText("Schatten").className).toContain("bg-info/10");
    expect(within(row("Recherche")).getByText("beratend").className).toContain("bg-ai/10");
    expect(within(row("Kritisch")).getByText("primär").className).toContain("bg-pos/10");
  });

  it("faerbt Beleg-Status nach Vertrag und markiert fehlenden Routenbericht als nicht belegt", () => {
    render(<AITransportPanel state={ready(fixture())} />);
    expect(within(row("Abwägung")).getByText("belegt").className).toContain("bg-pos/10");
    expect(within(row("Standard")).getByText("lückenhaft").className).toContain("bg-warn/10");
    expect(within(row("Masse")).getByText("keine Evidenz").className).toContain("bg-bg-2");
    const fehlt = within(row("Kritisch")).getByText("Bericht nicht belegt");
    expect(fehlt.closest("[title]")?.getAttribute("title")).toBe("Route steht nicht im Routenbericht");
  });

  it("zeigt Circuit offen rot, halboffen gelb, sonst zu", () => {
    render(<AITransportPanel state={ready(fixture())} />);
    const offen = within(row("Standard")).getByText("Circuit offen");
    expect(offen.closest("span[title]")?.className).toContain("bg-neg/10");
    expect(offen.closest("span[title]")?.getAttribute("title")).toContain("5 Fehler in Folge");
    const halb = within(row("Kritisch")).getByText("Circuit halboffen");
    expect(halb.closest("span[title]")?.className).toContain("bg-warn/10");
    expect(within(row("Abwägung")).getByText("Circuit zu")).toBeInTheDocument();
    expect(within(row("Masse")).getByText("Circuit zu")).toBeInTheDocument();
  });

  it("zeigt je Transport letzten Erfolg, Identitaet und veraltete Evidenz", () => {
    render(<AITransportPanel state={ready(fixture())} />);
    const std = row("Standard");
    const text = std.textContent ?? "";
    expect(text).toContain("LiteLLM");
    expect(text).toContain(formatDayTime("2026-09-10T14:59:00+00:00"));
    expect(text).toContain("Identität 100 %");
    expect(within(std).getByText(/Evidenz veraltet/)).toHaveTextContent("20 Tage");
    expect(text).toContain("5 Fehler");
    expect(within(row("Abwägung")).getByText(/95,5 %/)).toBeInTheDocument();
  });

  it("nennt fehlende Punkte im Klartext statt Rohschluesseln", () => {
    render(<AITransportPanel state={ready(fixture())} />);
    const std = row("Standard");
    expect(std).toHaveTextContent("Fehlt: LiteLLM: Evidenz zu alt · direkt: Identität unbelegt");
    expect(std.textContent).not.toContain("litellm:evidenzalter");
  });

  it("rendert jedes null als 'nicht belegt' mit Grund und nie als 0", () => {
    render(<AITransportPanel state={ready(fixture())} />);
    const beleg = screen.getByText("Beleg").parentElement as HTMLElement;
    const nb = within(beleg).getByText("nicht belegt");
    expect(nb.closest("[title]")?.getAttribute("title")).toBe("Zeile traegt keinen Zeitstempel");

    const direct = within(row("Standard")).getByTestId("evidence-direct");
    expect(direct.textContent).not.toMatch(/\b0 %/);
    const gruende = within(direct)
      .getAllByText("nicht belegt")
      .map((el) => el.closest("[title]")?.getAttribute("title"));
    expect(gruende).toEqual([
      "kein erfolgreicher Direktaufruf im Fenster",
      "keine Antwort mit belegter Identitaet",
    ]);
  });

  it("warnt deutlich, wenn der laufende Transport nicht dem Lock entspricht", () => {
    render(<AITransportPanel state={ready(fixture())} />);
    const alarm = screen.getByRole("alert");
    expect(alarm).toHaveTextContent("Laufender Transport entspricht nicht dem Lock – Neubau nötig");
    expect(alarm).toHaveTextContent("4d9c64c0aaaa");
    expect(alarm).toHaveTextContent("7a1b2c3dbbbb");
  });

  it("zeigt Kopf mit Proxy, Version, Release (12 Zeichen) und Inferenz-Decke", () => {
    const { container } = render(<AITransportPanel state={ready(fixture())} />);
    const text = container.textContent ?? "";
    expect(text).toContain("lebt");
    expect(text).toContain("HTTP 200");
    expect(text).toContain("1.102.1");
    expect(text).toContain("ca1a188c0123");
    expect(text).not.toContain(COMMIT);
    expect(text).toContain("Decke: beratend");
  });

  it("zeigt im Fuss Bericht, Fristen und abgekoppelte Schatten", () => {
    const { container } = render(<AITransportPanel state={ready(fixture())} />);
    const text = container.textContent ?? "";
    expect(text).toContain(`Bericht vom ${formatDayTime("2026-09-30T16:00:05+00:00")} (vor 30 min)`);
    expect(text).toContain("2 belegt · 1 lückenhaft · 2 ohne Evidenz");
    expect(text).toContain("Schatten-Nachfrist 1 s");
    expect(text).toContain("abgekoppelte Schatten 2/8");
    expect(within(row("Standard")).getByText(/Gesamtfrist 127,3 s/)).toBeInTheDocument();
  });

  it("hat keine Schalter, Buttons oder Eingaben, die etwas aendern", () => {
    const { container } = render(<AITransportPanel state={ready(fixture())} />);
    for (const role of ["button", "checkbox", "switch", "textbox", "combobox", "radio", "slider"]) {
      expect(screen.queryAllByRole(role)).toHaveLength(0);
    }
    expect(container.querySelector("form, input, select, textarea, button, a[href]")).toBeNull();
  });

  it("meldet einen toten Proxy", () => {
    const data = fixture();
    data.transport.proxy_alive = false;
    data.transport.proxy_status_code = null;
    data.transport.lock_matches = true;
    data.null_reasons["transport.proxy_status_code"] = "ConnectionError: refused";
    render(<AITransportPanel state={ready(data)} />);
    const alarm = screen.getByRole("alert");
    expect(alarm).toHaveTextContent("Proxy antwortet nicht");
    expect(alarm).toHaveTextContent("ConnectionError: refused");
    expect(screen.queryByText(/entspricht nicht dem Lock/)).toBeNull();
  });

  it("zeigt den Hinweis, wenn noch kein Routenbericht existiert", () => {
    const data = fixture();
    data.report = { available: false, generated_at: null, age_hours: null, status_counts: { BELEGT: 0, LUECKENHAFT: 0, KEINE_EVIDENZ: 0 } };
    data.null_reasons["report.generated_at"] = "Artefakt fehlt: kai-litellm-route-report noch nicht gelaufen";
    render(<AITransportPanel state={ready(data)} />);
    expect(
      screen.getByText("Noch kein Routenbericht – Timer kai-litellm-route-report läuft stündlich"),
    ).toBeInTheDocument();
  });

  it("zeigt den Ladezustand", () => {
    render(
      <AITransportPanel state={{ state: "loading", data: null, error: null, reload: vi.fn() }} />,
    );
    expect(screen.getByText(/KI-Transport \(LiteLLM\) wird geladen/)).toBeInTheDocument();
  });

  it.each([
    ["not_found", 404, /kennt \/health\/ai\/transport noch nicht/],
    ["unauthorized", 401, /neu anmelden/],
    ["server", 500, /Backend meldet einen Fehler/],
  ])("rendert den Fehlerzustand %s verstaendlich statt abzustuerzen", (kind, status, hint) => {
    const state = failed(kind, status);
    render(<AITransportPanel state={state} />);
    expect(screen.getByText("KI-Transport (LiteLLM) nicht verfügbar")).toBeInTheDocument();
    expect(screen.getByText(hint)).toBeInTheDocument();
    expect(screen.getByText(new RegExp(`HTTP ${status}`))).toBeInTheDocument();
    // Einziger Knopf im Fehlerfall: erneut LESEN. Nichts, was etwas aendert.
    const buttons = screen.getAllByRole("button");
    expect(buttons.map((b) => b.textContent?.trim())).toEqual(["Erneut laden"]);
    buttons[0].click();
    expect(state.reload).toHaveBeenCalledTimes(1);
  });
});
