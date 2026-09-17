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
 * Uebersicht haengen zwei daran (damals BackendStatusBanner, CommandHeader), also zwei
 * Anfragen alle 30 s auf ausgerechnet den Endpunkt, der in der Messung mit
 * 12,44 s fuer 322 Bytes der langsamste war — nicht weil der Handler teuer ist
 * (er macht ein paar stat-Aufrufe), sondern weil der Single-Worker-Event-Loop
 * von blockierenden Lesevorgaengen belegt ist. Ein zweiter paralleler Aufruf
 * verlaengert genau diese Warteschlange.
 *
 * 2026-09-16 (System-Audit P0-2): Gemessen auf dem Pi ~20 /health-Anfragen pro
 * Sekunde aus EINEM Browser-Tab (1,8 Mio/Tag, 200 MB Logs/Tag). Drei Ursachen,
 * die zusammen eine Schleife bildeten:
 *   1. `useSyncExternalStore` bekam bei jedem Render eine NEUE subscribe-
 *      Funktion -> React meldet ab und wieder an. In einem Commit laufen erst
 *      alle Cleanups, dann alle Subscriptions: der Zaehler faellt auf 0 und der
 *      naechste Subscribe pingte sofort (und brach die laufende Antwort ab).
 *   2. `emit` erzeugte auch bei unveraenderter Antwort ein neues Snapshot-
 *      Objekt -> jeder Tick renderte alle Abonnenten neu.
 *   3. Der Sofort-Ping beim 0->1-Uebergang hatte keinen Mindestabstand.
 * Jetzt: subscribe-Funktion je Intervall stabil (Modul-Cache), Snapshot nur bei
 * echter Aenderung, Sofort-Ping nur, wenn die letzte Antwort aelter als
 * MIN_REPING_MS ist, laufende Anfrage wird beim Abmelden nicht mehr verworfen.
 * Der Testfall "merely re-render" in useBackendHealth.test.ts brachte die alte
 * Fassung zum Absturz des Test-Workers (Endlosschleife) — das ist der Beweis.
 */
let status: BackendStatus = INITIAL;
const listeners = new Set<() => void>();
let timer: number | undefined;
let inFlight: AbortController | null = null;
let lastSettledAt = 0;

/** Unter dieser Frist loest ein neuer Abonnent KEINEN Sofort-Ping aus. */
const MIN_REPING_MS = 5_000;

function sameStatus(a: BackendStatus, b: BackendStatus): boolean {
  return a.state === b.state && a.version === b.version && a.detail === b.detail;
}

function emit(next: BackendStatus) {
  // Gleicher Zustand -> gleiches Objekt. useSyncExternalStore vergleicht per
  // Identitaet; ein neues Objekt mit altem Inhalt waere ein Render ohne Anlass.
  if (sameStatus(status, next)) return;
  status = next;
  for (const l of listeners) l();
}

async function ping(): Promise<void> {
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
    if (inFlight === ctrl) {
      inFlight = null;
      lastSettledAt = Date.now();
    }
  }
}

function subscribe(onChange: () => void, pollMs: number): () => void {
  listeners.add(onChange);
  if (listeners.size === 1) {
    // Sofort-Ping nur, wenn wirklich lange nichts kam. Ein Re-Subscribe
    // innerhalb weniger Millisekunden (React-Commit) darf keine Anfrage kosten.
    if (Date.now() - lastSettledAt > MIN_REPING_MS) void ping();
    timer = window.setInterval(() => void ping(), pollMs);
  }
  return () => {
    listeners.delete(onChange);
    if (listeners.size === 0) {
      if (timer != null) window.clearInterval(timer);
      timer = undefined;
      // Laufende Anfrage NICHT abbrechen: ein Re-Subscribe im selben Commit
      // wuerde sonst jede Antwort verwerfen und sofort neu fragen.
    }
  };
}

// Stabile subscribe-Funktion je Intervall — useSyncExternalStore darf nicht
// bei jedem Render eine neue Identitaet sehen (siehe Kopfkommentar, Ursache 1).
const SUBSCRIBERS = new Map<number, (onChange: () => void) => () => void>();
function subscriberFor(pollMs: number): (onChange: () => void) => () => void {
  let fn = SUBSCRIBERS.get(pollMs);
  if (!fn) {
    fn = (onChange) => subscribe(onChange, pollMs);
    SUBSCRIBERS.set(pollMs, fn);
  }
  return fn;
}

const getSnapshot = () => status;

export function useBackendHealth(pollMs: number = 30_000): BackendStatus {
  return useSyncExternalStore(subscriberFor(pollMs), getSnapshot, getSnapshot);
}

/** Nur fuer Tests: einen Poll-Tick ausloesen, ohne auf das Intervall zu warten. */
export function __pingForTests(): Promise<void> {
  return ping();
}

/** Nur fuer Tests: Modulzustand zuruecksetzen. */
export function __resetBackendHealthForTests(): void {
  if (timer != null) window.clearInterval(timer);
  timer = undefined;
  inFlight?.abort();
  inFlight = null;
  lastSettledAt = 0;
  listeners.clear();
  status = INITIAL;
}
