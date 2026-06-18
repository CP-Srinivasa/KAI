import { afterEach, describe, expect, it, vi } from "vitest";

import { isEnabled, resolveMode } from "./types";

// Regression-Guard (3x verschwunden): der TradingView-Chart ist TV-1 reine
// Visualisierung und muss standardmäßig AN bleiben, damit ein Deploy-Build
// (`vite build` ohne web/.env) ihn nicht stumm abschaltet. Opt-out ist explizit.
describe("tradingview isEnabled (opt-out, default ON)", () => {
  afterEach(() => vi.unstubAllEnvs());

  it("is ON when the flag is unset (default)", () => {
    vi.stubEnv("VITE_TRADINGVIEW_ENABLED", undefined as unknown as string);
    expect(isEnabled()).toBe(true);
  });

  it("is ON when the flag is empty", () => {
    vi.stubEnv("VITE_TRADINGVIEW_ENABLED", "");
    expect(isEnabled()).toBe(true);
  });

  it("stays ON for the legacy '1' value", () => {
    vi.stubEnv("VITE_TRADINGVIEW_ENABLED", "1");
    expect(isEnabled()).toBe(true);
  });

  it("opts OUT only for '0'", () => {
    vi.stubEnv("VITE_TRADINGVIEW_ENABLED", "0");
    expect(isEnabled()).toBe(false);
  });

  it("opts OUT for 'false' (case-insensitive)", () => {
    vi.stubEnv("VITE_TRADINGVIEW_ENABLED", "False");
    expect(isEnabled()).toBe(false);
  });
});

describe("tradingview resolveMode", () => {
  afterEach(() => vi.unstubAllEnvs());

  it("defaults to the no-license widget embed", () => {
    vi.stubEnv("VITE_TRADINGVIEW_CHART_MODE", undefined as unknown as string);
    expect(resolveMode()).toBe("widget");
  });

  it("honours the advanced mode when requested", () => {
    vi.stubEnv("VITE_TRADINGVIEW_CHART_MODE", "advanced");
    expect(resolveMode()).toBe("advanced");
  });
});
