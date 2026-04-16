import { useEffect, useRef, useState } from "react";
import { ApiError, fetchDashboardQuality, type DashboardQuality } from "./api";

export type QualityState =
  | { state: "loading"; data: null; error: null }
  | { state: "ready"; data: DashboardQuality; error: null }
  | { state: "error"; data: null; error: { kind: string; message: string } };

export function useDashboardQuality(refreshMs: number = 60_000): QualityState {
  const [state, setState] = useState<QualityState>({ state: "loading", data: null, error: null });
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      abortRef.current?.abort();
      const ctrl = new AbortController();
      abortRef.current = ctrl;
      try {
        const data = await fetchDashboardQuality(ctrl.signal);
        if (cancelled) return;
        setState({ state: "ready", data, error: null });
      } catch (e) {
        if (cancelled) return;
        if (e instanceof ApiError) {
          setState({
            state: "error",
            data: null,
            error: { kind: e.kind, message: e.message },
          });
        } else {
          setState({
            state: "error",
            data: null,
            error: { kind: "unknown", message: (e as Error).message },
          });
        }
      }
    }

    load();
    const id = window.setInterval(load, refreshMs);
    return () => {
      cancelled = true;
      abortRef.current?.abort();
      window.clearInterval(id);
    };
  }, [refreshMs]);

  return state;
}
