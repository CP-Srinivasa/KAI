import { afterEach, describe, expect, it, vi } from "vitest";

import {
  circuitSummary,
  evidenceStatusMeta,
  formatAgeHours,
  formatSeconds,
  formatShare,
  missingLabel,
  modeMeta,
  nullReason,
  routeMeta,
  shortHash,
  transportLabel,
  transportOrder,
} from "./aiTransport";
import { ApiError, _resetInflightForTests, fetchAiTransport } from "./api";

afterEach(() => {
  vi.unstubAllGlobals();
  _resetInflightForTests();
});

describe("fetchAiTransport", () => {
  it("liest GET /health/ai/transport", async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(
        new Response(JSON.stringify({ schema_version: "ai-transport/v1" }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const data = await fetchAiTransport();
    expect(data.schema_version).toBe("ai-transport/v1");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/health/ai/transport");
    expect(init.method).toBe("GET");
  });

  it("ein 404 wird als not_found gemeldet, damit das Panel den Release-Hinweis zeigt", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          new Response(JSON.stringify({ detail: "Not Found" }), {
            status: 404,
            headers: { "content-type": "application/json" },
          }),
        ),
      ),
    );
    const err = await fetchAiTransport().catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.kind).toBe("not_found");
    expect(err.status).toBe(404);
  });
});

describe("modeMeta", () => {
  it("faerbt die vier Modi nach Vertrag: aus grau, Schatten blau, beratend violett, primaer gruen", () => {
    expect(modeMeta("off")).toMatchObject({ label: "aus", tone: "muted" });
    expect(modeMeta("shadow")).toMatchObject({ label: "Schatten", tone: "info" });
    expect(modeMeta("advisory")).toMatchObject({ label: "beratend", tone: "ai" });
    expect(modeMeta("primary")).toMatchObject({ label: "primär", tone: "pos" });
  });

  it("ein unbekannter Modus wird nicht geraten, sondern roh und grau gezeigt", () => {
    expect(modeMeta("turbo")).toMatchObject({ label: "turbo", tone: "muted" });
  });
});

describe("evidenceStatusMeta", () => {
  it("BELEGT gruen, LUECKENHAFT gelb, KEINE_EVIDENZ grau", () => {
    expect(evidenceStatusMeta("BELEGT")).toMatchObject({ label: "belegt", tone: "pos" });
    expect(evidenceStatusMeta("LUECKENHAFT")).toMatchObject({ label: "lückenhaft", tone: "warn" });
    expect(evidenceStatusMeta("KEINE_EVIDENZ")).toMatchObject({ label: "keine Evidenz", tone: "muted" });
  });
});

describe("circuitSummary", () => {
  it("leer heisst zu, ohne Farbe", () => {
    expect(circuitSummary([])).toMatchObject({ label: "Circuit zu", tone: "muted" });
  });

  it("der schlimmste Zustand gewinnt: offen vor halboffen vor zu", () => {
    const s = circuitSummary([
      { upstream: "openai/gpt-5", state: "half_open", consecutive_failures: 3, probe_in_flight: true },
      { upstream: null, state: "open", consecutive_failures: 5, probe_in_flight: false },
    ]);
    expect(s).toMatchObject({ label: "Circuit offen", tone: "neg" });
    expect(s.detail).toContain("Alias gesamt: offen · 5 Fehler in Folge");
    expect(s.detail).toContain("openai/gpt-5: halboffen · 3 Fehler in Folge · Probe läuft");
  });

  it("nur halboffen ist gelb", () => {
    expect(
      circuitSummary([{ upstream: "x/y", state: "half_open", consecutive_failures: 1, probe_in_flight: false }]),
    ).toMatchObject({ label: "Circuit halboffen", tone: "warn" });
  });

  it("geschlossene Eintraege bleiben zu", () => {
    expect(
      circuitSummary([{ upstream: "x/y", state: "closed", consecutive_failures: 0, probe_in_flight: false }]),
    ).toMatchObject({ label: "Circuit zu", tone: "muted" });
  });
});

describe("missingLabel", () => {
  it("uebersetzt die Codes des Routenberichts in Klartext", () => {
    expect(missingLabel("litellm:evidenzalter")).toBe("LiteLLM: Evidenz zu alt");
    expect(missingLabel("litellm:evidence_age")).toBe("LiteLLM: Evidenz zu alt");
    expect(missingLabel("direct:kosten")).toBe("direkt: Kosten unbekannt");
    expect(missingLabel("litellm:letzter_erfolg")).toBe("LiteLLM: kein Erfolg belegt");
    expect(missingLabel("litellm:identitaet")).toBe("LiteLLM: Identität unbelegt");
    expect(missingLabel("litellm:circuit")).toBe("LiteLLM: Circuit-Zustand fehlt");
    expect(missingLabel("litellm:version")).toBe("LiteLLM: Transportversion fehlt");
  });

  it("unbekannte Codes bleiben lesbar statt zu verschwinden", () => {
    expect(missingLabel("unknown:neues_feld")).toBe("unbekannter Transport: neues feld");
    expect(missingLabel("ohne_transport")).toBe("ohne transport");
  });
});

describe("nullReason", () => {
  it("liefert den Grund oder sagt ehrlich, dass keiner uebermittelt wurde", () => {
    expect(nullReason({ "transport.version": "keine Zeile" }, "transport.version")).toBe("keine Zeile");
    expect(nullReason({}, "transport.version")).toBe("Backend nennt keinen Grund");
    expect(nullReason(undefined, "x")).toBe("Backend nennt keinen Grund");
  });
});

describe("Formatierung", () => {
  it("Sekunden deutsch, hoechstens eine Nachkommastelle", () => {
    expect(formatSeconds(30)).toBe("30 s");
    expect(formatSeconds(127.3)).toBe("127,3 s");
    expect(formatSeconds(1)).toBe("1 s");
  });

  it("Anteil 0..1 als Prozent", () => {
    expect(formatShare(1)).toBe("100 %");
    expect(formatShare(0.955)).toBe("95,5 %");
    expect(formatShare(0)).toBe("0 %");
  });

  it("Alter in Stunden lesbar", () => {
    expect(formatAgeHours(0.5)).toBe("30 min");
    expect(formatAgeHours(0)).toBe("1 min");
    expect(formatAgeHours(1.5)).toBe("2 h");
    expect(formatAgeHours(480.1)).toBe("20 Tage");
  });

  it("Hash auf 12 Zeichen gekuerzt", () => {
    expect(shortHash("ca1a188c0123456789abcdef0123456789abcdef")).toBe("ca1a188c0123");
  });
});

describe("Namen", () => {
  it("Routen und Transporte als Klartext", () => {
    expect(routeMeta("stt").label).toBe("Sprache zu Text");
    expect(routeMeta("bulk").label).toBe("Masse");
    expect(routeMeta("neu").label).toBe("neu");
    expect(transportLabel("litellm")).toBe("LiteLLM");
    expect(transportLabel("direct")).toBe("direkt");
    expect(transportLabel("unknown")).toBe("unbekannter Transport");
  });

  it("Transporte in fester Reihenfolge: LiteLLM, direkt, unbekannt, Rest", () => {
    expect(transportOrder(["zeta", "unknown", "direct", "litellm"])).toEqual([
      "litellm",
      "direct",
      "unknown",
      "zeta",
    ]);
  });
});
