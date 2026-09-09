import { useSyncExternalStore } from "react";
import { ApiError, fetchHealth } from "./api";

export type BackendStatus =
  | { state: "checking"; version: null; detail: null }
  | { state: "connected"; version: string; detail: null }
  | { state: "unauthorized"; version: null; detail: string }
  | { state: "offline"; version: null; detail: string };

const INITIAL: BackendStatus = { state: "checking", version: null, detail: null };

/**
 * EIN /health-Poller fuer alle Abonnenten.
 *
 * 2026-09-09: Der Hook startete pro Komponente einen eigenen Poller. Auf der
 * Uebersicht haengen zwei daran (BackendStatusBanner, CommandHeader), also zwei
 * Anfragen alle 30 s auf ausgerechnet den Endpunkt, der in der Messung mit
 * 12,44 s fuer 322 Bytes der langsamste war — nicht weil der Handler teuer ist
 * (er macht ein paar stat-Aufrufe), sondern weil der Single-Worker-Event-Loop
 * von blockierenden Lesevorgaengen belegt ist. Ein zweiter paralleler Aufruf
 * verlaengert genau diese Warteschlange.
 */
let status: BackendStatus = INITIAL;
const listeners = new Set<() => void>();
let timer: number | undefined;
let inFlight: AbortController | null = null;

function emit(next: BackendStatus) {
  status = next;
  for (const l of listeners) l();
}

async function ping() {
  // Ein laufender Ping wird nicht abgebrochen — sonst brachen sich bei
  // langsamen Antworten die Ticks gegenseitig ab und keiner kam je durch.
  if (inFlight) return;
  const ctrl = new AbortController();
  inFlight = ctrl;
  try {
    const res = await fetchHealth(ctrl.signal);
    emit({ state: "connected", version: res.version, detail: null });
  } catch (e) {
    if (ctrl.signal.aborted) return;
    if (e instanceof ApiError && (e.kind === "unauthorized" || e.kind === "forbidden")) {
      emit({ state: "unauthorized", version: null, detail: e.message });
    } else {
      const msg = e instanceof Error ? e.message : "network error";
      emit({ state: "offline", version: null, detail: msg });
    }
  } finally {
    if (inFlight === ctrl) inFlight = null;
  }
}

function subscribe(onChange: () => void, pollMs: number): () => void {
  listeners.add(onChange);
  if (listeners.size === 1) {
    void ping();
    timer = window.setInterval(() => void ping(), pollMs);
  }
  return () => {
    listeners.delete(onChange);
    if (listeners.size === 0) {
      if (timer != null) window.clearInterval(timer);
      timer = undefined;
      inFlight?.abort();
      inFlight = null;
    }
  };
}

export function useBackendHealth(pollMs: number = 30_000): BackendStatus {
  return useSyncExternalStore(
    (onChange) => subscribe(onChange, pollMs),
    () => status,
    () => status,
  );
}

/** Nur fuer Tests: Modulzustand zuruecksetzen. */
export function __resetBackendHealthForTests(): void {
  if (timer != null) window.clearInterval(timer);
  timer = undefined;
  inFlight = null;
  listeners.clear();
  status = INITIAL;
}
