import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "./api";

export type PollingState<T> =
  | { state: "loading"; data: null; error: null; reload: () => void }
  // fetchedAt: epoch-ms of the last successful fetch. Lets a panel show honest
  // "zuletzt aktualisiert vor Xs" even when the backend payload carries no
  // generated_at — no silent stale-freeze.
  | { state: "ready"; data: T; error: null; fetchedAt: number; reload: () => void }
  | { state: "error"; data: null; error: { kind: string; message: string }; reload: () => void };

export interface PollingOptions {
  intervalMs: number;
  pauseWhenHidden?: boolean;
  retry?: { maxAttempts: number; baseMs: number };
  /** Harte Zeitgrenze je Versuch. Siehe DEFAULT_REQUEST_TIMEOUT_MS. */
  timeoutMs?: number;
}

// 2026-09-08: Ohne Zeitgrenze plante `run()` den naechsten Tick erst NACH
// Aufloesung des Promise — ein Request, der nie zurueckkommt, legte das Polling
// des Panels dauerhaft still, ohne dass je ein Fehler flog. Genau das Muster aus
// project_kai_silent_loop_pattern.md (Push-Stream 46 h, Poll-Backstop 65 h),
// diesmal im Browser. 20 s liegt bewusst ueber den langsamsten beobachteten
// Antwortzeiten (Kaltstart-Aggregate ~7-10 s), damit die Grenze nur greift,
// wenn wirklich nichts mehr kommt.
export const DEFAULT_REQUEST_TIMEOUT_MS = 20_000;

// Transiente Fehler. "rate_limited" gehoert bewusst NICHT dazu: der Riegel in
// app/security/auth.py sperrt pro Client-IP fuer 300 s, ein Retry verlaengert
// die Sperre. "unauthorized"/"forbidden"/"not_found"/"bad_response" sind
// terminal — ein Wiederholen reproduziert nur denselben Fehlschlag.
const RETRYABLE_KINDS = new Set(["network", "server", "timeout"]);

export function usePolling<T>(
  fetcher: (signal: AbortSignal) => Promise<T>,
  opts: PollingOptions,
): PollingState<T> {
  const { intervalMs, pauseWhenHidden = true, retry, timeoutMs = DEFAULT_REQUEST_TIMEOUT_MS } = opts;

  // Stabile Identitaet ueber Re-Renders: Panels reichen `reload` an Buttons
  // weiter, und der aktuelle Lauf lebt im Effect. Der Ref ueberbrueckt beides.
  const runRef = useRef<(() => void) | null>(null);
  const reload = useCallback(() => {
    runRef.current?.();
  }, []);

  const [state, setState] = useState<PollingState<T>>({
    state: "loading",
    data: null,
    error: null,
    reload,
  });

  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  useEffect(() => {
    let cancelled = false;
    let timerId: number | null = null;
    let attempt = 0;
    let abortCtrl: AbortController | null = null;

    const clearTimer = () => {
      if (timerId != null) {
        window.clearTimeout(timerId);
        timerId = null;
      }
    };

    const schedule = (delayMs: number) => {
      clearTimer();
      if (cancelled) return;
      if (pauseWhenHidden && document.visibilityState === "hidden") return;
      timerId = window.setTimeout(run, delayMs);
    };

    async function run() {
      timerId = null;
      if (cancelled) return;
      if (pauseWhenHidden && document.visibilityState === "hidden") return;

      abortCtrl?.abort();
      abortCtrl = new AbortController();
      const ctrl = abortCtrl;

      // Zeitgrenze je Versuch, als echtes Rennen. `ctrl.abort()` allein reicht
      // NICHT: es wirkt nur, wenn der Fetcher das Signal beachtet — ein Fetcher,
      // der es ignoriert, laesst das `await` weiterhaengen und das Panel stirbt
      // still. Das Rennen macht die Grenze unabhaengig vom Fetcher-Verhalten;
      // der abort() daneben bricht den echten Request trotzdem ab.
      let timedOut = false;
      let timeoutId = 0;
      const deadline = new Promise<never>((_resolve, reject) => {
        timeoutId = window.setTimeout(() => {
          timedOut = true;
          ctrl.abort();
          reject(new Error("request timeout"));
        }, timeoutMs);
      });

      try {
        const data = await Promise.race([fetcherRef.current(ctrl.signal), deadline]);
        window.clearTimeout(timeoutId);
        if (cancelled) return;
        attempt = 0;
        setState({ state: "ready", data, error: null, fetchedAt: Date.now(), reload });
        schedule(intervalMs);
      } catch (e) {
        window.clearTimeout(timeoutId);
        if (cancelled) return;
        // Ein Abbruch durch unmount/replace ist KEIN Fehler — eine
        // Zeitueberschreitung dagegen schon, obwohl beide ueber denselben
        // AbortController laufen.
        if (ctrl.signal.aborted && !timedOut) return;

        const errInfo = timedOut
          ? {
              kind: "timeout",
              message: `Keine Antwort innerhalb von ${Math.round(timeoutMs / 1000)} s`,
            }
          : e instanceof ApiError
            ? { kind: e.kind, message: e.message }
            : { kind: "unknown", message: (e as Error).message };

        setState({ state: "error", data: null, error: errInfo, reload });

        const retryable = retry && RETRYABLE_KINDS.has(errInfo.kind);
        if (retryable && attempt < retry.maxAttempts) {
          const backoff = retry.baseMs * Math.pow(2, attempt);
          attempt += 1;
          schedule(backoff);
        } else {
          attempt = 0;
          schedule(intervalMs);
        }
      }
    }

    function onVisibility() {
      if (cancelled) return;
      if (document.visibilityState === "visible") {
        if (timerId == null) run();
      } else if (pauseWhenHidden) {
        clearTimer();
        abortCtrl?.abort();
      }
    }

    // `reload` aus dem Panel loest sofort einen Versuch aus und setzt den
    // Backoff zurueck — sonst wartet ein Klick noch die restliche Backoff-Zeit ab.
    runRef.current = () => {
      if (cancelled) return;
      attempt = 0;
      clearTimer();
      void run();
    };

    run();
    if (pauseWhenHidden) {
      document.addEventListener("visibilitychange", onVisibility);
    }

    return () => {
      cancelled = true;
      runRef.current = null;
      clearTimer();
      abortCtrl?.abort();
      if (pauseWhenHidden) {
        document.removeEventListener("visibilitychange", onVisibility);
      }
    };
  }, [intervalMs, pauseWhenHidden, retry?.maxAttempts, retry?.baseMs, timeoutMs, reload]);

  return state;
}
