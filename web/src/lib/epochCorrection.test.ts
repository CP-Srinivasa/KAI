import { describe, it, expect } from "vitest";
import {
  epochCorrectionNote,
  measurementCoversNow,
  type EpochCorrection,
} from "./epochCorrection";

// Der reale Vermerk aus app/execution/epoch_correction.py, gekuerzt auf die
// Felder, die die Oberflaeche liest. Zahlen bewusst die echten: der Fall, den
// diese Datei absichert, IST paper_v2_attested.
function notice(over: Partial<EpochCorrection> = {}): EpochCorrection {
  return {
    epoch_id: "paper_v2_attested",
    incident_ref: "DS-20260818-MOCK-EXIT",
    recorded_at_utc: "2026-08-18T00:00:00+00:00",
    summary: "Vier Closes gegen synthetische Mock-Preise gebucht.",
    detail: "Root cause: MockMarketDataAdapter …",
    verify_command: "python -m app.cli.main trading canonical-edge --json",
    measured_basis: "sum(trade_pnl_usd) seit Epochen-Reset; NICHT net-of-entry-fee.",
    measured_at_utc: "2026-08-18T17:30:00+00:00",
    measured_closes: 215,
    measured_booked_usd: 771.05,
    measured_contaminated_closes: 4,
    measured_contaminated_usd: 1701.54,
    measured_corrected_usd: -930.49,
    flips_sign: true,
    ...over,
  };
}

describe("epochCorrectionNote", () => {
  it("ohne Vermerk kein Hinweis — saubere Epochen bleiben unbehelligt", () => {
    expect(epochCorrectionNote(null)).toBeNull();
    expect(epochCorrectionNote(undefined)).toBeNull();
  });

  it("nennt gebuchte UND korrigierte Zahl, nie nur eine davon", () => {
    const n = epochCorrectionNote(notice())!;
    expect(n.bookedUsd).toBe(771.05);
    expect(n.correctedUsd).toBe(-930.49);
    expect(n.contaminatedCloses).toBe(4);
  });

  it("macht den Vorzeichenwechsel explizit — das ist die Kernaussage", () => {
    const n = epochCorrectionNote(notice())!;
    expect(n.flipsSign).toBe(true);
    expect(n.headline).toContain("Vorzeichen");
  });

  it("kennzeichnet einen Vermerk ohne Vorzeichenwechsel anders", () => {
    const n = epochCorrectionNote(
      notice({ measured_corrected_usd: 500.0, flips_sign: false }),
    )!;
    expect(n.flipsSign).toBe(false);
    expect(n.headline).not.toContain("Vorzeichen");
    expect(n.headline).toContain("korrigiert");
  });

  it("traegt Messdatum und Fallnummer mit — eine Zahl ohne Stand ist wertlos", () => {
    const n = epochCorrectionNote(notice())!;
    expect(n.measuredAtUtc).toBe("2026-08-18T17:30:00+00:00");
    expect(n.measuredCloses).toBe(215);
    expect(n.incidentRef).toBe("DS-20260818-MOCK-EXIT");
  });

  it("der Tooltip nennt Messbasis und Nachmess-Befehl", () => {
    const n = epochCorrectionNote(notice())!;
    expect(n.tooltip).toContain("net-of-entry-fee");
    expect(n.tooltip).toContain("canonical-edge");
  });

  it("uebersteht fehlende Zahlenfelder, statt NaN zu rendern", () => {
    const broken = { ...notice(), measured_booked_usd: undefined as unknown as number };
    expect(epochCorrectionNote(broken)).toBeNull();
  });
});

describe("measurementCoversNow", () => {
  // Der Kern der Ehrlichkeit: measured_* ist ein DATIERTER Schnappschuss, die
  // Epoche sammelt weiter. Die korrigierte Zahl darf niemals als aktueller
  // Stand gelesen werden, sobald neue Closes dazugekommen sind.
  it("gleiche Close-Zahl -> der Schnappschuss beschreibt das Jetzt", () => {
    expect(measurementCoversNow(notice(), 215)).toBe(true);
  });

  it("mehr Closes als gemessen -> Schnappschuss ist veraltet", () => {
    expect(measurementCoversNow(notice(), 216)).toBe(false);
    expect(measurementCoversNow(notice(), 400)).toBe(false);
  });

  it("unbekannte Live-Zahl -> im Zweifel veraltet, nicht optimistisch", () => {
    expect(measurementCoversNow(notice(), null)).toBe(false);
    expect(measurementCoversNow(notice(), undefined)).toBe(false);
  });

  it("weniger Closes als gemessen ist kein Beweis fuer Aktualitaet", () => {
    // Kann bei Epochen-Reset oder gefilterter Ansicht auftreten; die
    // Messung deckt dann eine ANDERE Population ab.
    expect(measurementCoversNow(notice(), 100)).toBe(false);
  });
});
