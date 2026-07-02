// KAI Persona — Risk Guards for Livetrade
// Spec source: docs/kai_persona/technical_ui_pack_v3_2.md §5
//                docs/kai_persona/final_execution_prompt_v3_4.md §12
//
// KAI may never let an unsafe Livetrade pass. These guards are checked BEFORE the renderer
// is allowed to emit a Livetrade card to the dashboard or Telegram.

import type { KaiSignalCardData } from "./types";

export interface KaiGuardResult {
  allowed: boolean;
  reasons: string[];
}

export function validateSignalForLivetrade(signal: KaiSignalCardData): KaiGuardResult {
  const reasons: string[] = [];

  if (signal.mode !== "LIVETRADE") {
    return { allowed: true, reasons: [] };
  }

  if (signal.risk === "CRITICAL") {
    reasons.push("Critical Risk blockiert Livetrading.");
  }

  if (signal.dataQuality === "LOW" || signal.dataQuality === "UNKNOWN") {
    reasons.push("Datenqualitaet reicht fuer Livetrading nicht aus.");
  }

  const stopLossNorm = (signal.stopLoss ?? "").toLowerCase();
  if (
    !signal.stopLoss ||
    stopLossNorm.includes("wartet") ||
    stopLossNorm.includes("not confirmed") ||
    stopLossNorm.includes("waiting")
  ) {
    reasons.push("Stop-Loss-Logik fehlt oder ist nicht bestaetigt.");
  }

  if (!signal.dataBasis.length) {
    reasons.push("Datenbasis fehlt.");
  }

  if (signal.confidence < 0 || signal.confidence > 100 || Number.isNaN(signal.confidence)) {
    reasons.push("Confidence liegt ausserhalb des erlaubten Bereichs 0-100.");
  }

  return {
    allowed: reasons.length === 0,
    reasons,
  };
}

// Generic invariants for every signal regardless of mode.
export function validateSignalInvariants(signal: KaiSignalCardData): KaiGuardResult {
  const reasons: string[] = [];

  if (!signal.asset || !signal.asset.includes("/")) {
    reasons.push("Asset fehlt oder hat kein Trading-Pair-Format (e.g. BTC/USDT).");
  }

  if (signal.confidence < 0 || signal.confidence > 100 || Number.isNaN(signal.confidence)) {
    reasons.push("Confidence ausserhalb 0-100.");
  }

  if (!["LONG", "SHORT", "NEUTRAL", "NO_TRADE"].includes(signal.direction)) {
    reasons.push("Direction ist kein gueltiger Wert.");
  }

  return { allowed: reasons.length === 0, reasons };
}
