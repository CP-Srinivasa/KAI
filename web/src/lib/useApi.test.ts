import { describe, it, expect } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { useApi } from "./useApi";
import { ApiError } from "./api";

describe("useApi retry (F-008)", () => {
  it("retries transient (server/network) errors and recovers", async () => {
    let calls = 0;
    const fetcher = async () => {
      calls += 1;
      if (calls < 3) throw new ApiError("server", 503, "/x", "boom");
      return { ok: true };
    };
    const { result } = renderHook(() =>
      useApi(fetcher, null, [], { maxAttempts: 3, baseMs: 10 }),
    );
    await waitFor(() => expect(result.current.state).toBe("ready"), { timeout: 2000 });
    expect(calls).toBe(3);
  });

  it("does NOT retry terminal (auth) errors — single call", async () => {
    let calls = 0;
    const fetcher = async () => {
      calls += 1;
      throw new ApiError("unauthorized", 401, "/x", "no");
    };
    const { result } = renderHook(() =>
      useApi(fetcher, null, [], { maxAttempts: 3, baseMs: 10 }),
    );
    await waitFor(() => expect(result.current.state).toBe("error"));
    // Allow time for any (incorrect) retry to fire.
    await new Promise((r) => setTimeout(r, 60));
    expect(calls).toBe(1);
  });

  it("without a retry config, a transient error is terminal (default behaviour unchanged)", async () => {
    let calls = 0;
    const fetcher = async () => {
      calls += 1;
      throw new ApiError("server", 503, "/x", "boom");
    };
    const { result } = renderHook(() => useApi(fetcher, null));
    await waitFor(() => expect(result.current.state).toBe("error"));
    await new Promise((r) => setTimeout(r, 60));
    expect(calls).toBe(1);
  });
});

// 2026-09-09: useApi meldete einen durch den naechsten Tick abgebrochenen
// Request als Netzwerkfehler. Da "network" retrybar ist, loeste der Abbruch
// einen Retry aus, den der uebernaechste Tick wieder abbrach — eine
// selbstverstaerkende Schleife auf genau den Endpunkten, die ohnehin zu
// langsam antworten (portfolio-snapshot ~8 s bei 30-s-Intervall). usePolling
// hatte den Guard seit 2026-09-08, useApi nicht.
describe("useApi abort handling (2026-09-09)", () => {
  it("an aborted request neither errors nor triggers a backoff retry", async () => {
    // Alle Versuche haengen, bis sie abgebrochen werden. Damit ist der
    // Intervall-Tick der EINZIGE legitime Ausloeser eines neuen Versuchs —
    // jeder Start darueber hinaus stammt aus dem Backoff-Retry, also aus der
    // Lawine. Ohne den Abort-Guard meldet apiGet den AbortError als
    // kind "network" (retrybar), der Retry wird abgebrochen, meldet wieder
    // "network", und so fort.
    let started = 0;
    const fetcher = (signal: AbortSignal) =>
      new Promise<{ ok: boolean }>((_resolve, reject) => {
        started += 1;
        signal.addEventListener("abort", () =>
          reject(new ApiError("network", 0, "/x", "Netzwerkfehler")),
        );
      });

    const { result, unmount } = renderHook(() =>
      useApi(fetcher, 100, [], { maxAttempts: 3, baseMs: 10 }),
    );
    await new Promise((r) => setTimeout(r, 500));
    unmount();

    // ~5 Intervall-Ticks in 500 ms. Grosszuegig nach oben abgegrenzt, aber weit
    // unter dem, was drei Backoff-Retries je Tick erzeugen wuerden.
    expect(started).toBeLessThanOrEqual(8);
    // Ein Abbruch ist kein Fehler: die Seite darf nicht auf Fehler umschlagen.
    expect(result.current.state).toBe("loading");
  });

  it("fails a hung request with kind 'timeout' instead of loading forever", async () => {
    const fetcher = () => new Promise<{ ok: boolean }>(() => {});
    const { result } = renderHook(() => useApi(fetcher, null, [], undefined, 40));
    await waitFor(() => expect(result.current.state).toBe("error"), { timeout: 2000 });
    expect(result.current.error?.kind).toBe("timeout");
  });
});
