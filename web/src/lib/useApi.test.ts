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
