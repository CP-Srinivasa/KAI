// Regression: der Fehlertext eines Panels muss die Ursache TRAGEN, nicht verdecken.
//
// Vorgeschichte (2026-09-08): `/operator/portfolio-snapshot` lieferte 48 h lang
// 4509 von 4509 Requests als 503 — mit vollstaendigem Fehler-Payload
// (code, message, request_id). Das Frontend machte daraus `String(detail)`,
// also woertlich "[object Object]". Der Operator las auf vier Panels
// `server · [object Object]`, waehrend die Ursache im Payload danebenlag.
// Serverseitig wird derselbe Fehler nur in die Antwort geschrieben und NICHT
// geloggt (app/api/routers/operator.py: _resolve_read_payload) — die Anzeige
// war die einzige Stelle, an der die Ursache je sichtbar wurde.

import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, apiGet } from "./api";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function mockFetch(res: Response): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(() => Promise.resolve(res)),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("apiGet Fehler-Normalisierung", () => {
  it("packt den Objekt-detail des Operator-Routers aus statt [object Object] zu zeigen", async () => {
    // exakt die Form aus _build_error_payload()
    mockFetch(
      jsonResponse(503, {
        detail: {
          error: {
            code: "portfolio_snapshot_unavailable",
            message: "Operator read surface unavailable: ValueError",
            request_id: "req_b4cc9b81ded8",
            correlation_id: "corr_1",
          },
          execution_enabled: false,
          write_back_allowed: false,
        },
      }),
    );

    const err = await apiGet("/operator/portfolio-snapshot").catch((e) => e);

    expect(err).toBeInstanceOf(ApiError);
    expect(err.message).not.toContain("[object Object]");
    expect(err.message).toBe("Operator read surface unavailable: ValueError");
    expect(err.code).toBe("portfolio_snapshot_unavailable");
    expect(err.requestId).toBe("req_b4cc9b81ded8");
    expect(err.kind).toBe("server");
  });

  it("behaelt einen String-detail unveraendert bei", async () => {
    mockFetch(jsonResponse(404, { detail: "Not Found" }));
    const err = await apiGet("/dashboard/api/nope").catch((e) => e);
    expect(err.message).toBe("Not Found");
    expect(err.kind).toBe("not_found");
    expect(err.code).toBeNull();
  });

  it("fasst FastAPI-422-Validierungsfehler lesbar zusammen", async () => {
    mockFetch(
      jsonResponse(422, {
        detail: [{ loc: ["query", "symbol"], msg: "field required", type: "value_error.missing" }],
      }),
    );
    const err = await apiGet("/dashboard/api/x").catch((e) => e);
    expect(err.message).not.toContain("[object Object]");
    expect(err.message).toBe("query.symbol: field required");
    expect(err.code).toBe("validation_error");
  });

  it("gibt 429 einen eigenen Kind, damit es nie als transient retried wird", async () => {
    mockFetch(jsonResponse(429, { detail: "Too many failed authentication attempts" }));
    const err = await apiGet("/operator/exposure-summary").catch((e) => e);
    expect(err.kind).toBe("rate_limited");
    expect(err.kind).not.toBe("server");
    expect(err.message).toBe("Too many failed authentication attempts");
  });

  it("faellt bei unbekannter Objektform auf gekuerztes JSON zurueck, nie auf [object Object]", async () => {
    mockFetch(jsonResponse(500, { detail: { unerwartet: "form", n: 3 } }));
    const err = await apiGet("/dashboard/api/y").catch((e) => e);
    expect(err.message).not.toContain("[object Object]");
    expect(err.message).toContain("unerwartet");
  });

  it("behaelt statusText, wenn der Body kein JSON ist (Cloudflare-HTML-Fehlerseite)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          new Response("<html>502 Bad Gateway</html>", {
            status: 502,
            statusText: "Bad Gateway",
            headers: { "content-type": "text/html" },
          }),
        ),
      ),
    );
    const err = await apiGet("/dashboard/api/z").catch((e) => e);
    expect(err.message).not.toContain("[object Object]");
    expect(err.kind).toBe("server");
  });
});
