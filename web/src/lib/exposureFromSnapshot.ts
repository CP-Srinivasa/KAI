import type { ExposureSummary, PortfolioSnapshot } from "./api";

/**
 * Leite die Exposure-Projektion aus dem Portfolio-Snapshot ab.
 *
 * 2026-09-09: /operator/exposure-summary war ein zweiter Netzwerk-Round-Trip auf
 * genau dieselbe Berechnung. Serverseitig ruft get_paper_exposure_summary
 * (canonical_read.py:249) build_paper_portfolio_snapshot_helper und wirft danach
 * alles ausser build_exposure_summary(snapshot) weg; build_exposure_summary
 * (portfolio_read.py:812) ist wiederum nur exposure_summary.to_json_dict() plus
 * generated_at, audit_path, available und error. Der Snapshot liefert alle vier
 * ohnehin mit — der zweite Aufruf kostete drei weitere Vollpaesse ueber ein
 * 5,2-MB-Audit und einen zweiten Marktdaten-Fan-out, fuer null zusaetzliche
 * Information.
 *
 * Gibt ``null`` zurueck, wenn das Backend keinen Exposure-Block mitschickt. Eine
 * synthetische Null-Exposure waere hier gefaehrlich: sie liest sich auf der
 * Risikoseite wie "kein Risiko".
 */
export function exposureFromSnapshot(snap: PortfolioSnapshot): ExposureSummary | null {
  const raw = snap.exposure_summary;
  if (!raw) return null;
  return {
    ...raw,
    generated_at: snap.generated_at,
    audit_path: snap.audit_path,
    available: snap.available ?? true,
    error: snap.error ?? null,
  };
}
