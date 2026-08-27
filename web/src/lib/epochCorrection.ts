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
  flips_sign: boolean;
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
 * Beschreibt der Schnappschuss noch den aktuellen Stand?
 *
 * ``measured_*`` wurde zu ``measured_at_utc`` erhoben; die Epoche sammelt
 * weiter. Sobald die Live-Zahl der Closes abweicht, deckt die Messung eine
 * ANDERE Population ab — die korrigierte Summe ist dann Geschichte, kein
 * Jetzt-Wert, und darf nicht als solcher gelesen werden.
 *
 * Im Zweifel (unbekannte Live-Zahl) lautet die Antwort ``false``: der
 * vorsichtige Fall ist der, der den Vorbehalt sichtbar laesst.
 */
export function measurementCoversNow(
  correction: EpochCorrection | null | undefined,
  liveClosedTotal: number | null | undefined,
): boolean {
  if (!correction) return false;
  if (!isFiniteNumber(liveClosedTotal)) return false;
  return liveClosedTotal === correction.measured_closes;
}
