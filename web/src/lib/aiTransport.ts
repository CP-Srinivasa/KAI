// KI-Transport (LiteLLM) — reine Abbildungen fuer das Panel auf der Seite System.
//
// Nichts hier bewertet. Modus, Beleg-Status und Circuit-Zustand kommen fertig
// vom Backend (Beleg-Status sogar nur aus dem stuendlichen Routenbericht); diese
// Datei macht daraus Klartext, Farbe und Tooltip — und sonst nichts. Unbekannte
// Werte werden roh und grau gezeigt statt geraten.

import type { AiTransportCircuit } from "@/lib/api";
import { DATE_LOCALE } from "@/lib/time";

export type AiTransportTone = "pos" | "neg" | "warn" | "info" | "ai" | "muted";

export type ToneMeta = { label: string; tone: AiTransportTone; hint: string };

/** Anzeige fuer jedes `null` im Vertrag. Nie 0, nie "—" ohne Grund. */
export const NOT_PROVEN = "nicht belegt";
const NO_REASON = "Backend nennt keinen Grund";

/** Grund zu einem `null` aus `null_reasons` — oder ehrlich: keiner uebermittelt. */
export function nullReason(reasons: Record<string, string> | null | undefined, key: string): string {
  const r = reasons?.[key];
  return r && r.trim() ? r : NO_REASON;
}

/* ---------- Routen & Transporte ---------- */

// Klartext + Zweck aus app/ai/routes.py. Der Rohschluessel bleibt als Alias
// (z. B. "kai-standard") und im Tooltip erreichbar.
const ROUTE_META: Record<string, { label: string; purpose: string }> = {
  bulk: { label: "Masse", purpose: "viele günstige Aufrufe" },
  standard: { label: "Standard", purpose: "Analyse · Chat" },
  reasoning: { label: "Abwägung", purpose: "Konsens mehrerer Meinungen" },
  critical: { label: "Kritisch", purpose: "Operator-Absicht" },
  stt: { label: "Sprache zu Text", purpose: "Sprachnachrichten" },
  research: { label: "Recherche", purpose: "Beratungstext" },
};

export function routeMeta(route: string): { label: string; purpose: string } {
  return ROUTE_META[route] ?? { label: route, purpose: "" };
}

const TRANSPORT_LABEL: Record<string, string> = {
  litellm: "LiteLLM",
  direct: "direkt",
  unknown: "unbekannter Transport",
};

export function transportLabel(name: string): string {
  return TRANSPORT_LABEL[name] ?? name;
}

/** Feste Anzeigereihenfolge: LiteLLM, direkt, unbekannt, dann der Rest alphabetisch. */
export function transportOrder(names: string[]): string[] {
  const rank = (n: string) => {
    const i = ["litellm", "direct", "unknown"].indexOf(n);
    return i < 0 ? 99 : i;
  };
  return [...names].sort((a, b) => rank(a) - rank(b) || a.localeCompare(b));
}

/* ---------- Modus ---------- */

// Semantik aus app/ai/modes.py (ADR 0017).
const MODE_META: Record<string, ToneMeta> = {
  off: { label: "aus", tone: "muted", hint: "Direkter KAI-Pfad antwortet, LiteLLM ist nicht beteiligt." },
  shadow: { label: "Schatten", tone: "info", hint: "Direkter Pfad antwortet, LiteLLM läuft ohne Wirkung mit." },
  advisory: {
    label: "beratend",
    tone: "ai",
    hint: "LiteLLM liefert nur Research-Text, ohne Ausführungsmacht.",
  },
  primary: {
    label: "primär",
    tone: "pos",
    hint: "LiteLLM trägt diese Route, der direkte Anbieter ist Fallback.",
  },
};

export function modeMeta(mode: string | null | undefined): ToneMeta {
  if (mode == null) return { label: NOT_PROVEN, tone: "muted", hint: "" };
  return MODE_META[mode] ?? { label: mode, tone: "muted", hint: "unbekannter Modus" };
}

/* ---------- Beleg-Status (nur aus dem Routenbericht) ---------- */

const EVIDENCE_META: Record<string, ToneMeta> = {
  BELEGT: { label: "belegt", tone: "pos", hint: "Jeder genutzte Transport belegt alle Pflichtfelder." },
  LUECKENHAFT: { label: "lückenhaft", tone: "warn", hint: "Mindestens ein Pflichtfeld fehlt." },
  KEINE_EVIDENZ: {
    label: "keine Evidenz",
    tone: "muted",
    hint: "Keine einzige Telemetriezeile für diese Route.",
  },
};

export function evidenceStatusMeta(status: string | null | undefined): ToneMeta {
  if (status == null) return { label: NOT_PROVEN, tone: "muted", hint: "" };
  return EVIDENCE_META[status] ?? { label: status, tone: "muted", hint: "unbekannter Beleg-Status" };
}

/* ---------- Circuit ---------- */

const CIRCUIT_RANK: Record<string, number> = { open: 2, half_open: 1 };
const CIRCUIT_STATE_LABEL: Record<string, string> = { open: "offen", half_open: "halboffen", closed: "zu" };

export function circuitSummary(entries: AiTransportCircuit[] | null | undefined): ToneMeta & {
  detail: string;
} {
  const list = entries ?? [];
  const worst = list.reduce((acc, e) => Math.max(acc, CIRCUIT_RANK[e.state] ?? 0), 0);
  const detail =
    list.length === 0
      ? "Kein Circuit-Zustand in diesem Prozess – alles zu."
      : list
          .map((e) => {
            const parts = [
              `${e.upstream ?? "Alias gesamt"}: ${CIRCUIT_STATE_LABEL[e.state] ?? e.state}`,
              `${e.consecutive_failures ?? NOT_PROVEN} Fehler in Folge`,
            ];
            if (e.probe_in_flight) parts.push("Probe läuft");
            return parts.join(" · ");
          })
          .join("\n");
  if (worst === 2) return { label: "Circuit offen", tone: "neg", hint: "", detail };
  if (worst === 1) return { label: "Circuit halboffen", tone: "warn", hint: "", detail };
  return { label: "Circuit zu", tone: "muted", hint: "", detail };
}

/* ---------- fehlende Pflichtfelder ---------- */

// Vokabular aus scripts/litellm_route_report/engine.py ("<transport>:<feld>").
const MISSING_FIELD: Record<string, string> = {
  letzter_erfolg: "kein Erfolg belegt",
  identitaet: "Identität unbelegt",
  kosten: "Kosten unbekannt",
  retries: "Retry-Angabe fehlt",
  fallback: "Fallback-Angabe fehlt",
  circuit: "Circuit-Zustand fehlt",
  version: "Transportversion fehlt",
  evidenzalter: "Evidenz zu alt",
  evidence_age: "Evidenz zu alt",
};

export function missingLabel(code: string): string {
  const at = code.indexOf(":");
  const transport = at >= 0 ? code.slice(0, at) : null;
  const field = at >= 0 ? code.slice(at + 1) : code;
  const text = MISSING_FIELD[field] ?? field.replace(/_/g, " ");
  return transport ? `${transportLabel(transport)}: ${text}` : text;
}

/* ---------- Formatierung ---------- */

const ONE_DECIMAL = new Intl.NumberFormat(DATE_LOCALE, { maximumFractionDigits: 1 });

export function formatSeconds(seconds: number): string {
  return `${ONE_DECIMAL.format(seconds)} s`;
}

/** Anteil 0..1 als Prozent, hoechstens eine Nachkommastelle. */
export function formatShare(share: number): string {
  return `${ONE_DECIMAL.format(Math.round(share * 1000) / 10)} %`;
}

/** Alter in Stunden (vom Backend gerechnet, nicht von der Browseruhr). */
export function formatAgeHours(hours: number): string {
  if (hours < 1) return `${Math.max(1, Math.round(hours * 60))} min`;
  if (hours < 48) return `${Math.round(hours)} h`;
  return `${Math.round(hours / 24)} Tage`;
}

export function shortHash(value: string, length = 12): string {
  return value.slice(0, length);
}
