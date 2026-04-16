import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

export type TradingMode = "paper" | "live" | "sim";
export type Timeframe = "24h" | "7d" | "30d" | "90d";
export const TIMEFRAMES: readonly Timeframe[] = ["24h", "7d", "30d", "90d"] as const;
export const TF_DAYS: Record<Timeframe, number> = { "24h": 1, "7d": 7, "30d": 30, "90d": 90 };

type AppStateCtx = {
  mode: TradingMode;
  setMode: (m: TradingMode) => void;
  confirmLive: boolean;
  setConfirmLive: (v: boolean) => void;
  sizeCapPct: number;
  setSizeCapPct: (v: number) => void;
  cooldownSec: number;
  setCooldownSec: (v: number) => void;
  timeframe: Timeframe;
  setTimeframe: (tf: Timeframe) => void;
};

const Ctx = createContext<AppStateCtx | null>(null);
const KEY = "kai-appstate";

type Persisted = {
  mode: TradingMode;
  confirmLive: boolean;
  sizeCapPct: number;
  cooldownSec: number;
  timeframe: Timeframe;
};

function readInitial(): Persisted {
  try {
    const raw = localStorage.getItem(KEY);
    if (raw) return { ...defaults(), ...JSON.parse(raw) };
  } catch {}
  return defaults();
}

function defaults(): Persisted {
  return { mode: "paper", confirmLive: true, sizeCapPct: 5, cooldownSec: 30, timeframe: "30d" };
}

export function AppStateProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<Persisted>(readInitial);

  useEffect(() => {
    try {
      localStorage.setItem(KEY, JSON.stringify(state));
    } catch {}
  }, [state]);

  const setMode = useCallback((mode: TradingMode) => setState((s) => ({ ...s, mode })), []);
  const setConfirmLive = useCallback((confirmLive: boolean) => setState((s) => ({ ...s, confirmLive })), []);
  const setSizeCapPct = useCallback((sizeCapPct: number) => setState((s) => ({ ...s, sizeCapPct })), []);
  const setCooldownSec = useCallback((cooldownSec: number) => setState((s) => ({ ...s, cooldownSec })), []);
  const setTimeframe = useCallback((timeframe: Timeframe) => setState((s) => ({ ...s, timeframe })), []);

  const value = useMemo<AppStateCtx>(
    () => ({ ...state, setMode, setConfirmLive, setSizeCapPct, setCooldownSec, setTimeframe }),
    [state, setMode, setConfirmLive, setSizeCapPct, setCooldownSec, setTimeframe],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAppState() {
  const c = useContext(Ctx);
  if (!c) throw new Error("useAppState outside provider");
  return c;
}

export function modeTone(mode: TradingMode): "warn" | "neg" | "info" {
  if (mode === "live") return "neg";
  if (mode === "sim") return "info";
  return "warn";
}
