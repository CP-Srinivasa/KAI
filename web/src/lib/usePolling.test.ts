// usePolling: Zeitgrenze und Wiederholen-Knopf.
//
// Vorgeschichte (2026-09-08): `run()` plante den naechsten Tick erst NACH
// Aufloesung des Promise. Ein Request ohne Antwort legte das Panel damit
// dauerhaft still — kein Fehler, kein weiterer Versuch, nur ein Panel, das
// nie wiederkommt. Dasselbe Muster wie die stillen Loop-Tode serverseitig
// (project_kai_silent_loop_pattern.md), hier im Browser.
// Und: PollingState trug kein `reload`, weshalb 21 Panels technisch keinen
// Wiederholen-Knopf anbieten konnten — dem Operator blieb nur F5.

import { renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ApiError } from "./api";
import { usePolling } from "./usePolling";

describe("usePolling Zeitgrenze", () => {
  it("bricht einen haengenden Request ab und meldet ihn als timeout", async () => {
    // Fetcher, der NIE aufloest — ohne Zeitgrenze bliebe der Hook ewig auf "loading".
    const fetcher = () => new Promise<never>(() => {});

    const { result } = renderHook(() =>
      usePolling(fetcher, { intervalMs: 10_000, pauseWhenHidden: false, timeoutMs: 50 }),
    );

    await waitFor(() => expect(result.current.state).toBe("error"), { timeout: 2000 });
    expect(result.current.error?.kind).toBe("timeout");
  });

  it("macht nach einer Zeitueberschreitung weiter, statt still zu sterben", async () => {
    let calls = 0;
    const fetcher = (): Promise<{ ok: boolean }> => {
      calls += 1;
      if (calls === 1) return new Promise<never>(() => {}); // erster Versuch haengt
      return Promise.resolve({ ok: true });
    };

    const { result } = renderHook(() =>
      usePolling(fetcher, {
        intervalMs: 10_000,
        pauseWhenHidden: false,
        timeoutMs: 50,
        retry: { maxAttempts: 3, baseMs: 10 },
      }),
    );

    await waitFor(() => expect(result.current.state).toBe("ready"), { timeout: 3000 });
    expect(calls).toBeGreaterThanOrEqual(2);
  });
});

describe("usePolling reload", () => {
  it("stellt in jedem Zustand ein reload bereit", async () => {
    const fetcher = async () => ({ ok: true });
    const { result } = renderHook(() =>
      usePolling(fetcher, { intervalMs: 10_000, pauseWhenHidden: false }),
    );
    expect(typeof result.current.reload).toBe("function");
    await waitFor(() => expect(result.current.state).toBe("ready"));
    expect(typeof result.current.reload).toBe("function");
  });

  it("holt auf Knopfdruck sofort neu, ohne den Intervall abzuwarten", async () => {
    let calls = 0;
    const fetcher = async () => {
      calls += 1;
      return { n: calls };
    };
    const { result } = renderHook(() =>
      usePolling(fetcher, { intervalMs: 60_000, pauseWhenHidden: false }),
    );
    await waitFor(() => expect(result.current.state).toBe("ready"));
    expect(calls).toBe(1);

    result.current.reload();
    await waitFor(() => expect(calls).toBe(2), { timeout: 2000 });
  });

  it("bietet reload auch im Fehlerzustand an — sonst bleibt nur F5", async () => {
    let calls = 0;
    const fetcher = async () => {
      calls += 1;
      throw new ApiError("server", 503, "/x", "kaputt");
    };
    const { result } = renderHook(() =>
      usePolling(fetcher, { intervalMs: 60_000, pauseWhenHidden: false }),
    );
    await waitFor(() => expect(result.current.state).toBe("error"));
    const before = calls;
    result.current.reload();
    await waitFor(() => expect(calls).toBeGreaterThan(before), { timeout: 2000 });
  });

  it("wiederholt 429 NICHT — ein Retry verlaengert die 300-s-Sperre", async () => {
    let calls = 0;
    const fetcher = async () => {
      calls += 1;
      throw new ApiError("rate_limited", 429, "/x", "Too many failed authentication attempts");
    };
    renderHook(() =>
      usePolling(fetcher, {
        intervalMs: 60_000,
        pauseWhenHidden: false,
        retry: { maxAttempts: 3, baseMs: 10 },
      }),
    );
    await waitFor(() => expect(calls).toBe(1));
    await new Promise((r) => setTimeout(r, 120));
    expect(calls).toBe(1);
  });
});
