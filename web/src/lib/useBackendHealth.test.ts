import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";

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
});
