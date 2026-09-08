// KAI PAY v0.1 — Status-Polling für EINE Zahlungsanforderung.
//
// Bewusst nicht `usePolling`/`useApi`: die pollen unbegrenzt weiter. Hier
// endet das Polling hart, sobald der Vertrag einen Endzustand liefert
// (SETTLED|EXPIRED|FAILED) oder die Anfrage selbst terminal scheitert
// (404/401/403). Netz-/Serverfehler halten das Polling am Leben und werden
// sichtbar gemacht — nie stiller Fehlzustand.

import { useEffect, useRef, useState } from "react";
import { ApiError, fetchPayRequest, type PayRequest } from "@/lib/api";
import { describeError, isTerminalStatus } from "@/lib/pay";

export const PAY_POLL_MS = 3_000;

/** Fehlerarten, bei denen ein weiterer Versuch dasselbe Ergebnis brächte. */
const STOP_ON_ERROR_KIND: ReadonlySet<string> = new Set(["not_found", "unauthorized", "forbidden"]);

export type PayPollState = {
  request: PayRequest | null;
  /** Letzter Fehler der Statusabfrage; wird beim nächsten Erfolg gelöscht. */
  error: string | null;
  /** true, solange noch ein Timer/Fetch aktiv ist. */
  polling: boolean;
};

export function usePayRequestPolling(
  paymentId: string | null,
  seed: PayRequest | null,
  intervalMs: number = PAY_POLL_MS,
  onTerminal?: (request: PayRequest) => void,
): PayPollState {
  const seedRef = useRef(seed);
  seedRef.current = seed;
  const onTerminalRef = useRef(onTerminal);
  onTerminalRef.current = onTerminal;

  const [state, setState] = useState<PayPollState>(() => ({
    request: seed,
    error: null,
    polling: paymentId != null && !isTerminalStatus(seed?.status),
  }));

  useEffect(() => {
    if (!paymentId) {
      setState({ request: null, error: null, polling: false });
      return;
    }
    const initial = seedRef.current?.payment_id === paymentId ? seedRef.current : null;
    if (initial && isTerminalStatus(initial.status)) {
      // Ein bereits abgeschlossener Request (z.B. aus der Liste) wird nicht gepollt.
      setState({ request: initial, error: null, polling: false });
      return;
    }
    setState({ request: initial, error: null, polling: true });

    let cancelled = false;
    let timer: number | null = null;
    let ctrl: AbortController | null = null;

    const tick = async () => {
      timer = null;
      if (cancelled) return;
      ctrl = new AbortController();
      const mine = ctrl;
      try {
        const r = await fetchPayRequest(paymentId, mine.signal);
        if (cancelled) return;
        const terminal = isTerminalStatus(r.status);
        setState({ request: r, error: null, polling: !terminal });
        if (terminal) {
          onTerminalRef.current?.(r);
          return;
        }
      } catch (e) {
        if (cancelled || mine.signal.aborted) return;
        const kind = e instanceof ApiError ? e.kind : "unknown";
        const stop = STOP_ON_ERROR_KIND.has(kind);
        const message = describeError(e);
        setState((s) => ({ request: s.request, error: message, polling: !stop }));
        if (stop) return;
      }
      timer = window.setTimeout(tick, intervalMs);
    };

    void tick();

    return () => {
      cancelled = true;
      if (timer != null) window.clearTimeout(timer);
      ctrl?.abort();
    };
  }, [paymentId, intervalMs]);

  return state;
}
