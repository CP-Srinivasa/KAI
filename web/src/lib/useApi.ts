import { useEffect, useRef, useState } from "react";
import { ApiError } from "./api";

export type AsyncState<T> =
  | { state: "loading"; data: null; error: null; reload: () => void }
  | { state: "ready"; data: T; error: null; reload: () => void }
  | { state: "error"; data: null; error: { kind: string; message: string; status: number }; reload: () => void };

// Generischer Polling-Hook für async-Fetcher. Jeder aktive Dashboard-Bereich nutzt
// diesen Hook, damit Lade-, Fehler- und Refresh-Verhalten überall identisch sind.
export function useApi<T>(
  fetcher: (signal: AbortSignal) => Promise<T>,
  refreshMs: number | null = 30_000,
  deps: readonly unknown[] = [],
): AsyncState<T> {
  const [state, setState] = useState<AsyncState<T>>({
    state: "loading",
    data: null,
    error: null,
    reload: () => {},
  });
  const abortRef = useRef<AbortController | null>(null);
  const tickRef = useRef(0);

  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      abortRef.current?.abort();
      const ctrl = new AbortController();
      abortRef.current = ctrl;
      try {
        const data = await fetcher(ctrl.signal);
        if (cancelled) return;
        setState({ state: "ready", data, error: null, reload });
      } catch (e) {
        if (cancelled) return;
        if (e instanceof ApiError) {
          setState({
            state: "error",
            data: null,
            error: { kind: e.kind, message: e.message, status: e.status },
            reload,
          });
        } else {
          setState({
            state: "error",
            data: null,
            error: { kind: "unknown", message: (e as Error).message, status: 0 },
            reload,
          });
        }
      }
    };

    const reload = () => {
      tickRef.current += 1;
      void load();
    };

    void load();
    let id: number | undefined;
    if (refreshMs != null && refreshMs > 0) {
      id = window.setInterval(load, refreshMs);
    }
    return () => {
      cancelled = true;
      abortRef.current?.abort();
      if (id != null) window.clearInterval(id);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshMs, ...deps]);

  return state;
}
