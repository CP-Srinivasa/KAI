/**
 * H6 Dev-Guard: kein stiller localhost-Fallback fuer das Dashboard.
 *
 * WARUM: `vite.config.ts` fiel bisher ohne `VITE_KAI_API_BASE` still auf
 * `http://127.0.0.1:8000` zurueck. Wer `npm run dev` startet und daneben ein
 * lokales `uvicorn` laufen hat, proxied damit unbemerkt auf eine LOKALE,
 * historische `data/dev.db` — und liest Mai-/Juli-Zustaende als waeren sie
 * aktuell. Gemessen am 31.08.2026 (Local Data Staleness Audit).
 *
 * WAS SICH NICHT AENDERT: Der Produktionspfad ist bereits korrekt. `apiBase`
 * steuert ausschliesslich den **Dev-Server-Proxy** und wird nie ins Bundle
 * injiziert; das Frontend fetcht Same-Origin relativ (Asset-Base `/dashboard/`).
 * Im gebauten Bundle stehen nachweislich 0 Treffer fuer `127.0.0.1`,
 * `localhost`, `192.168` und `:8000` — auf dem Pi wie lokal. Deshalb darf hier
 * fuer `command === "build"` NICHTS gefordert werden: eine Pflicht-Variable im
 * Build wuerde den CI-Job `web` sofort brechen, ohne ein Risiko zu beseitigen.
 *
 * INVARIANTE: NO_SILENT_LOCALHOST_FALLBACK — localhost nur nach bewusstem
 * Opt-in, und dann sichtbar. ABSOLUTE_PI_URL_REQUIRED bleibt bewusst NEIN:
 * die UI wird nicht an eine konkrete Pi-IP gekoppelt.
 */

export const BASE_VAR = "VITE_KAI_API_BASE";
export const OPT_IN_VAR = "KAI_ALLOW_LOCAL_DEV_BACKEND";
export const LOCAL_DEV_BASE = "http://127.0.0.1:8000";

export type ApiBaseResolution =
  /** Explizit gesetzt — der Normalfall fuer Entwicklung gegen den Pi. */
  | { kind: "explicit"; base: string }
  /** Bewusstes Opt-in auf ein lokales Backend. Muss sichtbar gemacht werden. */
  | { kind: "local-dev-optin"; base: string }
  /** Build/Preview: der Proxy wird nicht benutzt, es gibt keine Base. */
  | { kind: "not-needed"; base: undefined };

/** Fehlertext bewusst als Konstante: der Test prueft, dass er beide Variablen nennt. */
export function missingBaseMessage(): string {
  return [
    "",
    "  Dev-Server abgebrochen: keine API-Base gesetzt.",
    "",
    `  Setze ${BASE_VAR} auf das Backend, gegen das entwickelt werden soll`,
    "  (z. B. die Pi-Adresse) — ODER erlaube ein lokales Backend ausdruecklich mit",
    `  ${OPT_IN_VAR}=1.`,
    "",
    "  Kein stiller Rueckfall auf " + LOCAL_DEV_BASE + ": ein lokales Backend liest",
    "  eine lokale, moeglicherweise historische Datenbank und stellt sie im",
    "  Dashboard als aktuellen Zustand dar.",
    "",
  ].join("\n");
}

/** Banner fuer den Opt-in-Fall — der Modus darf nicht unbemerkt bleiben. */
export function localDevBanner(): string {
  return [
    "",
    "  ############################################################",
    "  #  LOCAL DEV · NON-AUTHORITATIVE DATA                     #",
    `  #  ${OPT_IN_VAR}=1 -> Proxy auf ${LOCAL_DEV_BASE}`,
    "  #  Angezeigte Werte stammen NICHT von der autoritativen",
    "  #  Runtime (Pi). Nicht fuer Analyse, PnL oder Truth nutzen.",
    "  ############################################################",
    "",
  ].join("\n");
}

/**
 * @param command  Vite-Kommando: "serve" (Dev-Server) oder "build".
 * @param env      Ergebnis von loadEnv (bzw. process.env-artiges Objekt).
 * @param isPreview `vite preview` bedient ein fertiges Bundle — kein Proxy noetig.
 * @throws wenn der Dev-Server ohne Base und ohne Opt-in gestartet wird.
 */
export function resolveApiBase(
  command: string,
  env: Record<string, string | undefined>,
  isPreview = false,
): ApiBaseResolution {
  const explicit = (env[BASE_VAR] ?? "").trim();
  if (explicit) return { kind: "explicit", base: explicit };

  // Build und Preview benutzen den Proxy nicht -> hier nichts fordern.
  if (command !== "serve" || isPreview) return { kind: "not-needed", base: undefined };

  if ((env[OPT_IN_VAR] ?? "").trim() === "1") {
    return { kind: "local-dev-optin", base: LOCAL_DEV_BASE };
  }

  throw new Error(missingBaseMessage());
}
