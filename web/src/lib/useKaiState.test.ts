// Ein gescheiterter Request darf nicht wie eine Messung aussehen.
//
// Vorgeschichte (2026-09-08): Der catch-Zweig ERFAND bei Netzwerkfehlern und 5xx
// einen OFFLINE-Zustand und setzte ihn als `state: "ready"`. Der Command-Header
// zeigte daraufhin `Live · OFFLINE`, als waere OFFLINE ein gemessener
// Laufzeitzustand des Knotens — tatsaechlich war nur der Request gescheitert.
// Das ist die Fehlerklasse "Fehler sieht aus wie Messung", die teurer ist als
// ein sichtbarer Ausfall.

import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useKaiState } from "./useKaiState";

afterEach(() => {
  vi.unstubAllGlobals();
});

function stubFetch(impl: () => Promise<Response>): void {
  vi.stubGlobal("fetch", vi.fn(impl));
}

describe("useKaiState meldet Ausfaelle als Ausfall", () => {
  it("meldet einen Netzwerkfehler als error, nicht als ready/OFFLINE", async () => {
    stubFetch(() => Promise.reject(new TypeError("Failed to fetch")));

    const { result } = renderHook(() => useKaiState());

    await waitFor(() => expect(result.current.state).toBe("error"));
    expect(result.current.state).not.toBe("ready");
  });

  it("meldet 5xx als error, nicht als ready/OFFLINE", async () => {
    stubFetch(() =>
      Promise.resolve(new Response("{}", { status: 503, headers: { "content-type": "application/json" } })),
    );

    const { result } = renderHook(() => useKaiState());

    await waitFor(() => expect(result.current.state).toBe("error"));
  });

  it("meldet 401 weiterhin als error (unveraendert)", async () => {
    stubFetch(() => Promise.resolve(new Response("{}", { status: 401 })));

    const { result } = renderHook(() => useKaiState());

    await waitFor(() => expect(result.current.state).toBe("error"));
    expect(result.current.state === "error" && result.current.error.kind).toBe("unauthorized");
  });

  it("liefert einen echten Zustand weiterhin als ready durch", async () => {
    stubFetch(() =>
      Promise.resolve(
        new Response(JSON.stringify({ state: "IDLE", is_stub: true, phase: 1 }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      ),
    );

    const { result } = renderHook(() => useKaiState());

    await waitFor(() => expect(result.current.state).toBe("ready"));
    expect(result.current.state === "ready" && result.current.data.state).toBe("IDLE");
  });
});
