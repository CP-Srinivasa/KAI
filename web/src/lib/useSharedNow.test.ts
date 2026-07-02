import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useSharedNow, __resetSharedNowForTests } from "./useSharedNow";

describe("useSharedNow", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(1_000_000);
    __resetSharedNowForTests();
  });
  afterEach(() => {
    __resetSharedNowForTests();
    vi.useRealTimers();
  });

  it("advances on the shared 5s tick", () => {
    const { result } = renderHook(() => useSharedNow());
    expect(result.current).toBe(1_000_000);
    act(() => {
      vi.advanceTimersByTime(5_000);
    });
    expect(result.current).toBe(1_005_000);
  });

  it("drives multiple subscribers from one clock (identical value)", () => {
    const a = renderHook(() => useSharedNow());
    const b = renderHook(() => useSharedNow());
    act(() => {
      vi.advanceTimersByTime(5_000);
    });
    expect(a.result.current).toBe(1_005_000);
    expect(b.result.current).toBe(1_005_000);
    expect(a.result.current).toBe(b.result.current);
  });

  it("stops ticking after the last subscriber unmounts (no further updates)", () => {
    const { result, unmount } = renderHook(() => useSharedNow());
    act(() => {
      vi.advanceTimersByTime(5_000);
    });
    expect(result.current).toBe(1_005_000);
    unmount();
    // With no subscribers the shared interval is torn down; advancing time must
    // not throw or update a stale component.
    act(() => {
      vi.advanceTimersByTime(10_000);
    });
    expect(result.current).toBe(1_005_000);
  });
});
