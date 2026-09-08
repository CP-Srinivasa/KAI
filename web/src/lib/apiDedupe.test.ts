// Gleichzeitige GETs auf denselben Pfad teilen sich EINE Antwort.
//
// 2026-09-08: Ein Dashboard-Mount feuerte 29 Requests auf 22 Endpoints — 7 davon
// Duplikate, weil mehrere Panels dieselbe Quelle brauchen. Der Server dedupliziert
// laengst (SingleFlightCache), der Client nicht; die Ersparnis verpuffte auf der
// Leitung. Bei einem Browser-Limit von ~6 Verbindungen je Origin verzoegerten die
// Duplikate die uebrigen Panels aktiv.

import { afterEach, describe, expect, it, vi } from "vitest";

import { _resetInflightForTests, apiGet } from "./api";

afterEach(() => {
  vi.unstubAllGlobals();
  _resetInflightForTests();
});

function deferredFetch() {
  let release!: (body: unknown) => void;
  const calls: string[] = [];
  const gate = new Promise<unknown>((res) => {
    release = res;
  });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      calls.push(url);
      const body = await gate;
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }),
  );
  return { calls, release };
}

describe("apiGet Deduplizierung", () => {
  it("bedient drei gleichzeitige Aufrufer aus EINEM Request", async () => {
    const { calls, release } = deferredFetch();

    const a = apiGet<{ n: number }>("/dashboard/api/quality");
    const b = apiGet<{ n: number }>("/dashboard/api/quality");
    const c = apiGet<{ n: number }>("/dashboard/api/quality");

    release({ n: 7 });
    const [ra, rb, rc] = await Promise.all([a, b, c]);

    expect(calls.length).toBe(1);
    expect(ra.n).toBe(7);
    expect(rb.n).toBe(7);
    expect(rc.n).toBe(7);
  });

  it("teilt NICHT ueber verschiedene Pfade hinweg", async () => {
    const { calls, release } = deferredFetch();

    const a = apiGet("/operator/portfolio-snapshot");
    const b = apiGet("/operator/exposure-summary");

    release({ ok: true });
    await Promise.all([a, b]);

    expect(calls.length).toBe(2);
  });

  it("teilt NICHT mehr, sobald die Antwort da ist", async () => {
    const { calls, release } = deferredFetch();
    const first = apiGet("/health");
    release({ ok: true });
    await first;

    const { calls: calls2, release: release2 } = deferredFetch();
    const second = apiGet("/health");
    release2({ ok: true });
    await second;

    expect(calls.length).toBe(1);
    expect(calls2.length).toBe(1);
  });

  it("teilt nicht, wenn ein Aufrufer eigene Header setzt", async () => {
    const { calls, release } = deferredFetch();

    const a = apiGet("/dashboard/api/quality");
    const b = apiGet("/dashboard/api/quality", { headers: { "X-Sonder": "1" } });

    release({ ok: true });
    await Promise.all([a, b]);

    expect(calls.length).toBe(2);
  });

  it("ein Abbruch reisst die Antwort der Mitleser nicht weg", async () => {
    const { release } = deferredFetch();
    const ctrl = new AbortController();

    const shared = apiGet<{ n: number }>("/dashboard/api/lightning");
    ctrl.abort();
    const aborted = apiGet("/dashboard/api/lightning", { signal: ctrl.signal }).catch(
      (e) => e as Error,
    );

    release({ n: 3 });

    expect((await shared).n).toBe(3);
    expect(await aborted).toBeInstanceOf(Error);
  });

  it("ein Fehler wird an alle Mitleser gereicht und raeumt die Flugphase", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(JSON.stringify({ detail: "kaputt" }), {
          status: 503,
          headers: { "content-type": "application/json" },
        }),
      ),
    );

    const a = apiGet("/operator/portfolio-snapshot").catch((e) => e);
    const b = apiGet("/operator/portfolio-snapshot").catch((e) => e);
    const [ea, eb] = await Promise.all([a, b]);

    expect(ea).toBeInstanceOf(Error);
    expect(eb).toBeInstanceOf(Error);
    // Nach dem Fehler darf kein Eintrag zurueckbleiben, sonst bekaeme der
    // naechste Versuch ewig denselben alten Fehlschlag serviert.
    const again = await apiGet("/operator/portfolio-snapshot").catch((e) => e);
    expect(again).toBeInstanceOf(Error);
  });
});
