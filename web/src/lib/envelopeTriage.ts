export type TriageRecord = {
  timestamp_utc?: string | null;
  status?: string | null;
  stage?: string | null;
};

export type TriageCounts = {
  accepted: number;
  duplicate: number;
  needs: number;
  rejected: number;
};

export const TRIAGE_WINDOW_DAYS = 30;

/**
 * Zaehle Envelope-Ausgaenge in einem gleitenden Fenster.
 *
 * 2026-09-09: Die Vorgaengerfunktion filterte auf "heute UTC". Die
 * Premium-Quelle ist seit 2026-07-31 still (Operator-Entscheid CANCEL), also
 * standen alle vier Kacheln seit fuenf Wochen dauerhaft auf 0 — ununterscheidbar
 * von einem Defekt. Ein Zaehler, dessen Fenster kuerzer ist als der Abstand
 * zwischen zwei Ereignissen, misst nichts; er zeigt nur Null.
 */
export function windowCounts(
  records: readonly TriageRecord[],
  days: number = TRIAGE_WINDOW_DAYS,
  now: number = Date.now(),
): TriageCounts {
  const start = now - days * 24 * 60 * 60 * 1000;
  const counts: TriageCounts = { accepted: 0, duplicate: 0, needs: 0, rejected: 0 };
  for (const rec of records) {
    if (!rec.timestamp_utc) continue;
    const ts = Date.parse(rec.timestamp_utc);
    if (Number.isNaN(ts) || ts < start) continue;
    if (rec.status === "ok" || rec.stage === "accepted") counts.accepted++;
    else if (rec.status === "duplicate") counts.duplicate++;
    else if (rec.status === "rejected" || rec.status === "blocked") counts.rejected++;
    else if (rec.stage === "voice_confirm_gate" || rec.status === "draft_pending") counts.needs++;
  }
  return counts;
}

/**
 * Zeitstempel des juengsten Envelopes, oder ``null``.
 *
 * Die ehrlichere Hauptzahl als "Heute: 0": sie unterscheidet "heute nichts
 * angekommen" von "seit fuenf Wochen nichts angekommen".
 */
export function lastReceivedAt(records: readonly TriageRecord[]): string | null {
  let best: number | null = null;
  let bestRaw: string | null = null;
  for (const rec of records) {
    if (!rec.timestamp_utc) continue;
    const ts = Date.parse(rec.timestamp_utc);
    if (Number.isNaN(ts)) continue;
    if (best === null || ts > best) {
      best = ts;
      bestRaw = rec.timestamp_utc;
    }
  }
  return bestRaw;
}
