// KAI PAY v0.1 — reine Logik ohne React (Tests: pay.test.ts).
//
// Hier steht alles, was die Seite `pages/Pay.tsx` entscheiden muss, ohne DOM:
// Status → Ton/Terminalität, Formular-Validierung gegen den API-Vertrag,
// QR-Payload (GROSSBUCHSTABEN), Countdown-Arithmetik und die lesbare
// Fehlerdarstellung. Die Seite rendert nur, was hier herauskommt.

import { ApiError, type PayRequest, type PayRequestCreate, type PayRequestCreated, type PayRequestStatus } from "@/lib/api";

export type PayTone = "pos" | "neg" | "warn" | "muted";

/** Endzustände laut Vertrag — hier hört das Polling auf. */
const TERMINAL: ReadonlySet<string> = new Set<PayRequestStatus>(["SETTLED", "EXPIRED", "FAILED"]);

export function isTerminalStatus(status: string | null | undefined): boolean {
  return status != null && TERMINAL.has(status);
}

export function payStatusTone(status: string | null | undefined): PayTone {
  switch (status) {
    case "WAITING":
      return "warn";
    case "SETTLED":
      return "pos";
    case "EXPIRED":
    case "FAILED":
      return "neg";
    default:
      return "muted";
  }
}

/** Anzeigetext des Status — die Vertragswörter selbst, nichts Übersetztes. */
export function payStatusLabel(status: string | null | undefined): string {
  if (!status) return "UNBEKANNT";
  return String(status).toUpperCase();
}

// ---------------------------------------------------------------------------
// Formular → Vertrag. Grenzen aus dem API-Vertrag: amount_sat ≥ 1 (Ganzzahl),
// description 1..140, reference ≤ 64 (optional).
// ---------------------------------------------------------------------------

export const PAY_DESCRIPTION_MAX = 140;
export const PAY_REFERENCE_MAX = 64;

export type PayFormInput = { amountSat: string; description: string; reference: string };
export type PayFormErrors = Partial<Record<keyof PayFormInput, string>>;
export type PayFormResult =
  | { ok: true; body: PayRequestCreate }
  | { ok: false; errors: PayFormErrors };

export function validatePayForm(input: PayFormInput): PayFormResult {
  const errors: PayFormErrors = {};

  const amountRaw = input.amountSat.trim();
  // Nur Ziffern — "5.00", "5,5", "1e3" oder ein Vorzeichen sind KEIN sat-Betrag.
  const amount = /^\d+$/.test(amountRaw) ? Number(amountRaw) : NaN;
  if (!Number.isSafeInteger(amount) || amount < 1) {
    errors.amountSat = "Betrag muss eine ganze Zahl ≥ 1 sat sein";
  }

  const description = input.description.trim();
  if (description.length < 1 || description.length > PAY_DESCRIPTION_MAX) {
    errors.description = `Beschreibung: 1 bis ${PAY_DESCRIPTION_MAX} Zeichen`;
  }

  const reference = input.reference.trim();
  if (reference.length > PAY_REFERENCE_MAX) {
    errors.reference = `Referenz: höchstens ${PAY_REFERENCE_MAX} Zeichen`;
  }

  if (Object.keys(errors).length > 0) return { ok: false, errors };

  const body: PayRequestCreate = { amount_sat: amount, description };
  if (reference) body.reference = reference;
  return { ok: true, body };
}

// ---------------------------------------------------------------------------
// QR-Payload. BOLT11 ist bech32 und damit case-insensitiv; in GROSSBUCHSTABEN
// kodiert der QR-Encoder im alphanumerischen Modus (kleinerer Code, robusteres
// Scannen — Empfehlung aus der BOLT11-Spezifikation). Das `lightning:`-Schema
// ist nach RFC 3986 ebenfalls case-insensitiv, also darf die ganze URI hoch.
// ---------------------------------------------------------------------------

export function qrPayload(lightningUri: string): string {
  return lightningUri.trim().toUpperCase();
}

// ---------------------------------------------------------------------------
// Beträge / Zeiten
// ---------------------------------------------------------------------------

export function formatSats(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return `${Math.trunc(value).toLocaleString("de-DE")} sat`;
}

/** Verbleibende Sekunden bis `expiresAt` (nie negativ); null bei unlesbarem Stempel. */
export function remainingSeconds(expiresAt: string | null | undefined, nowMs: number): number | null {
  if (!expiresAt) return null;
  const ms = Date.parse(expiresAt);
  if (!Number.isFinite(ms)) return null;
  return Math.max(0, Math.floor((ms - nowMs) / 1000));
}

/** "mm:ss" unter einer Stunde, sonst "h:mm:ss"; null → "—". */
export function formatCountdown(seconds: number | null): string {
  if (seconds == null) return "—";
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const mm = String(m).padStart(2, "0");
  const ss = String(sec).padStart(2, "0");
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

// ---------------------------------------------------------------------------
// Antwort-Normalisierung
// ---------------------------------------------------------------------------

function looksLikePayRequest(x: unknown): x is PayRequest {
  return (
    typeof x === "object" &&
    x !== null &&
    typeof (x as { payment_id?: unknown }).payment_id === "string" &&
    typeof (x as { status?: unknown }).status === "string"
  );
}

/** `GET /pay/requests?limit=N`: Array oder {items|requests:[…]} → PayRequest[]; alles andere → []. */
export function normalizePayList(raw: unknown): PayRequest[] {
  let arr: unknown[] = [];
  if (Array.isArray(raw)) arr = raw;
  else if (raw && typeof raw === "object") {
    const o = raw as { items?: unknown; requests?: unknown };
    if (Array.isArray(o.items)) arr = o.items;
    else if (Array.isArray(o.requests)) arr = o.requests;
  }
  return arr.filter(looksLikePayRequest);
}

/** POST-Antwort → Ansichtsobjekt (dieselbe Form wie `GET /pay/requests/{id}`). */
export function toPayRequest(created: PayRequestCreated): PayRequest {
  return {
    payment_id: created.payment_id,
    status: created.status,
    amount_sat: created.amount_sat,
    paid_amount_sat: null,
    paid_at: null,
    reference: created.reference ?? null,
    description: created.description,
    created_at: created.created_at,
    expires_at: created.expires_at,
    last_error: null,
  };
}

// ---------------------------------------------------------------------------
// Fehler lesbar machen — nie stiller Fehlzustand.
// ---------------------------------------------------------------------------

export function describeError(e: unknown): string {
  if (e instanceof ApiError) {
    const http = e.status > 0 ? `HTTP ${e.status}` : "keine Verbindung";
    return `${http} · ${e.kind} · ${e.message}`;
  }
  if (e instanceof Error) return e.message || "unbekannter Fehler";
  return String(e ?? "unbekannter Fehler");
}

/** 404 auf `/pay/health` (oder enabled=false) heißt: Feature am Server aus. */
export function isPayDisabledError(err: { kind: string; status?: number } | null | undefined): boolean {
  return err?.kind === "not_found";
}
