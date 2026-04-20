import { useEffect, useRef, useState } from "react";
import { ApiError, fetchDashboardProvenance, type DashboardProvenance } from "./api";

export type ProvenanceState =
  | { state: "loading"; data: null; error: null }
  | { state: "ready"; data: DashboardProvenance; error: null }
  | { state: "error"; data: null; error: { kind: string; message: string } };

export function useDashboardProvenance(refreshMs: number = 60_000): ProvenanceState {
  const [state, setState] = useState<ProvenanceState>({
    state: "loading",
    data: null,
    error: null,
  });
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      abortRef.current?.abort();
      const ctrl = new AbortController();
      abortRef.current = ctrl;
      try {
        const data = await fetchDashboardProvenance(ctrl.signal);
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
