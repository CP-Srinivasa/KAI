import { describe, it, expect } from "vitest";
import { liveDotProps } from "./freshness";

describe("liveDotProps", () => {
  it("passes loading/error through with null generatedAt", () => {
    expect(liveDotProps({ state: "loading" })).toEqual({ state: "loading", generatedAt: null });
    expect(liveDotProps({ state: "error" })).toEqual({ state: "error", generatedAt: null });
  });

  it("prefers the server generated_at over the client fetchedAt when present", () => {
    const out = liveDotProps({ state: "ready", fetchedAt: 1_000 }, "2026-06-22T10:00:00Z");
    expect(out).toEqual({ state: "ready", generatedAt: "2026-06-22T10:00:00Z" });
  });

  it("falls back to fetchedAt (ISO) when no server timestamp", () => {
    const out = liveDotProps({ state: "ready", fetchedAt: 0 });
    expect(out.state).toBe("ready");
    expect(out.generatedAt).toBe(new Date(0).toISOString());
  });

  it("treats a blank server timestamp as absent and uses fetchedAt", () => {
    const out = liveDotProps({ state: "ready", fetchedAt: 5_000 }, "   ");
    expect(out.generatedAt).toBe(new Date(5_000).toISOString());
  });

  it("yields null generatedAt when ready but neither source is available", () => {
    expect(liveDotProps({ state: "ready" })).toEqual({ state: "ready", generatedAt: null });
  });
});
