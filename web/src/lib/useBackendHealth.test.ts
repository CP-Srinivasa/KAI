import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { useState } from "react";

// 2026-09-09: useBackendHealth startete PRO Komponente einen eigenen Poller.
// Auf der Uebersicht haengen zwei daran (BackendStatusBanner und
// CommandHeader), also zwei /health-Anfragen alle 30 s. Ausgerechnet /health
// war der langsamste Endpunkt der Messung — 12,44 s fuer 322 Bytes, weil der
// Single-Worker-Loop von blockierenden Lesevorgaengen belegt war. Ein zweiter
// paralleler Aufruf verschlimmert genau das.
vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return { ...actual, fetchHealth: vi.fn() };
});

import { fetchHealth } from "./api";
import { useBackendHealth, __resetBackendHealthForTests } from "./useBackendHealth";

const mocked = fetchHealth as unknown as ReturnType<typeof vi.fn>;

beforeEach(() => {
  vi.clearAllMocks();
  __resetBackendHealthForTests();
  mocked.mockResolvedValue({ status: "ok", version: "0.1.0" });
});

afterEach(() => {
  __resetBackendHealthForTests();
});

describe("useBackendHealth", () => {
  it("issues ONE request no matter how many components subscribe", async () => {
    const a = renderHook(() => useBackendHealth());
    const b = renderHook(() => useBackendHealth());
    const c = renderHook(() => useBackendHealth());

    await waitFor(() => expect(a.result.current.state).toBe("connected"));
    expect(b.result.current.state).toBe("connected");
    expect(c.result.current.state).toBe("connected");
    expect(mocked).toHaveBeenCalledTimes(1);
  });

  it("still reports offline to every subscriber", async () => {
    mocked.mockRejectedValue(new Error("boom"));
    const a = renderHook(() => useBackendHealth());
    const b = renderHook(() => useBackendHealth());
    await waitFor(() => expect(a.result.current.state).toBe("offline"));
    expect(b.result.current.state).toBe("offline");
    expect(mocked).toHaveBeenCalledTimes(1);
  });

  // 2026-09-16 (System-Audit P0-2): Auf dem Pi kamen ~20 /health-Anfragen pro
  // Sekunde aus EINEM Browser-Tab, 1,8 Mio am Tag. Ursache: der Hook gab
  // useSyncExternalStore bei jedem Render eine NEUE subscribe-Funktion. React
  // meldet dann ab und wieder an; in einem Commit laufen erst alle Cleanups,
  // dann alle Subscriptions — der Zaehler faellt auf 0, der naechste Subscribe
  // pingt sofort, die Antwort erzeugt ein neues Snapshot-Objekt, das rendert,
  // und die Runde beginnt von vorn, nur durch die Netzlatenz gebremst.
  it("does NOT ping again when the subscribing components merely re-render", async () => {
    // Zwei Abonnenten in EINEM Baum, damit Cleanup/Subscribe wie auf der
    // Uebersicht in einem Commit laufen (Banner + Lage-Leiste).
    const { result } = renderHook(() => {
      const [n, setN] = useState(0);
      const a = useBackendHealth();
      const b = useBackendHealth();
      return { a, b, bump: () => setN((v) => v + 1), n };
    });
    await waitFor(() => expect(result.current.a.state).toBe("connected"));
    expect(mocked).toHaveBeenCalledTimes(1);

    for (let i = 0; i < 25; i++) {
      act(() => result.current.bump());
    }
    // Ein paar Ticks warten, damit ein etwaiger Fehl-Ping auflaufen koennte.
    await new Promise((r) => setTimeout(r, 30));

    expect(result.current.n).toBe(25);
    expect(result.current.b.state).toBe("connected");
    expect(mocked).toHaveBeenCalledTimes(1);
  });

  it("keeps the snapshot identity when the reported state is unchanged", async () => {
    const { result, rerender } = renderHook(() => useBackendHealth());
    await waitFor(() => expect(result.current.state).toBe("connected"));
    const first = result.current;

    // Ein zweiter Ping mit identischer Antwort darf keinen neuen Snapshot
    // erzeugen — sonst rendert jeder Abonnent bei jedem Tick ohne Anlass.
    await act(async () => {
      await __pingForTests();
    });
    rerender();
    expect(mocked).toHaveBeenCalledTimes(2);
    expect(result.current).toBe(first);
  });
});

import { __pingForTests } from "./useBackendHealth";
