/**
 * Korrektur-Vermerke zu einer Paper-Buch-Epoche.
 *
 * Ein gebuchtes Ergebnis wird nie umgeschrieben — das Audit ist append-only.
 * Steht aber fest, dass eine gebuchte Zahl auf erfundenen Eingaben beruht, ist
 * es der Fehler, sie blank zu zitieren. Das Backend fuehrt den Beweis in
 * ``app/execution/epoch_correction.py`` und haengt ihn an die Epoche, damit
 * JEDE Oberflaeche den Vorbehalt mit der Zahl zusammen tragen kann.
 *
 * Diese Datei ist die Lese-Seite davon. Sie rechnet nichts aus: sie reicht die
 * gemessenen Zahlen weiter und macht sichtbar, dass sie ein DATIERTER
 * Schnappschuss sind. Genau daran ist der Vermerk bisher gescheitert — er
 * existierte in der API, aber keine Oberflaeche zeigte ihn an.
 */

/** Payload aus ``epoch_correction_payload`` (app/execution/epoch_correction.py). */
export type EpochCorrection = {
  epoch_id: string;
  incident_ref: string;
  recorded_at_utc: string;
  summary: string;
  detail: string;
  verify_command: string;
  measured_basis: string;
  measured_at_utc: string;
  measured_closes: number;
  measured_booked_usd: number;
  measured_contaminated_closes: number;
  measured_contaminated_usd: number;
  measured_corrected_usd: number;
  /**
   * Identitaet des Quellzustands, ueber den gemessen wurde. ``null`` heisst:
   * die Deckung ist NICHT beweisbar — dann bleibt die Zahl historisch.
   */
  measured_source_sha256?: string | null;
  flips_sign: boolean;
};

/** Der aktuell sichtbare Quellzustand, gegen den eine Messung geprueft wird. */
export type SourceIdentity = {
  closeCount: number | null | undefined;
  sourceSha256: string | null | undefined;
};

export type EpochCorrectionNote = {
  /** Eine Zeile fuer Badge/Helper — nennt den Vorzeichenwechsel beim Namen. */
  headline: string;
  flipsSign: boolean;
  bookedUsd: number;
  correctedUsd: number;
  contaminatedCloses: number;
  measuredAtUtc: string;
  measuredCloses: number;
  incidentRef: string;
  /** Langtext fuer title=: Befund, Messbasis, Nachmess-Befehl. */
  tooltip: string;
};

function isFiniteNumber(v: unknown): v is number {
  return typeof v === "number" && Number.isFinite(v);
}

/**
 * Der Vermerk als anzeigbare Notiz — oder ``null``, wenn die Epoche sauber ist.
 *
 * Fehlt eine der Zahlen, gibt es lieber KEINE Notiz als eine mit ``NaN``: ein
 * halb gerenderter Korrekturhinweis ist schlimmer als keiner, weil er
 * Vollstaendigkeit suggeriert.
 */
export function epochCorrectionNote(
  correction: EpochCorrection | null | undefined,
): EpochCorrectionNote | null {
  if (!correction) return null;
  const booked = correction.measured_booked_usd;
  const corrected = correction.measured_corrected_usd;
  if (!isFiniteNumber(booked) || !isFiniteNumber(corrected)) return null;

  const flipsSign = correction.flips_sign === true;
  const headline = flipsSign
    ? "Korrektur-Vermerk: Vorzeichen dreht sich"
    : "Korrektur-Vermerk: Zahl korrigiert";

  const tooltip = [
    correction.summary,
    `Messbasis: ${correction.measured_basis}`,
    `Nachmessen: ${correction.verify_command}`,
  ]
    .filter(Boolean)
    .join("\n\n");

  return {
    headline,
    flipsSign,
    bookedUsd: booked,
    correctedUsd: corrected,
    contaminatedCloses: correction.measured_contaminated_closes,
    measuredAtUtc: correction.measured_at_utc,
    measuredCloses: correction.measured_closes,
    incidentRef: correction.incident_ref,
    tooltip,
  };
}

/**
 * Deckt diese Messung exakt den aktuell sichtbaren Quellzustand ab?
 *
 * Die naheliegende Antwort — "gleiche Anzahl Closes, also derselbe Stand" —
 * ist falsch. Eine Anzahl kann zufaellig wieder uebereinstimmen, waehrend
 * darunter andere Ereignisse liegen: ein Epochen-Reset, eine nachtraegliche
 * Quarantaene, die Reparatur einer Zeile. Anzahl ist eine Kennzahl, keine
 * Identitaet.
 *
 * Verlangt wird darum BEIDES: derselbe Quell-Digest UND dieselbe Anzahl.
 * Fehlt der Digest — auf einer der beiden Seiten —, lautet die Antwort
 * ``false``. Nicht "wahrscheinlich noch aktuell", sondern schlicht: nicht
 * beweisbar, also historisch.
 *
 * Praktische Folge heute: der Vermerk vom 2026-08-18 wurde ohne Digest
 * aufgenommen. Diese Funktion gibt fuer ihn IMMER ``false`` zurueck, und die
 * Oberflaeche kennzeichnet seine Zahlen ausnahmslos als Messung mit Datum.
 * Das ist die gewollte Vorsicht, kein Defekt.
 */
export function measurementCoversNow(
  correction: EpochCorrection | null | undefined,
  live: SourceIdentity | null | undefined,
): boolean {
  if (!correction || !live) return false;
  const measuredSha = correction.measured_source_sha256;
  if (typeof measuredSha !== "string" || measuredSha.length === 0) return false;
  if (typeof live.sourceSha256 !== "string" || live.sourceSha256.length === 0) return false;
  if (measuredSha !== live.sourceSha256) return false;
  if (!isFiniteNumber(live.closeCount)) return false;
  return live.closeCount === correction.measured_closes;
}
