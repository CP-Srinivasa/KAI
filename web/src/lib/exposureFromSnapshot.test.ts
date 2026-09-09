import { describe, it, expect } from "vitest";
import { exposureFromSnapshot } from "./exposureFromSnapshot";
import type { PortfolioSnapshot } from "./api";

// 2026-09-09: Die Uebersicht rief /operator/portfolio-snapshot 3x und
// /operator/exposure-summary 2x pro Mount, alle 30 s. Beide Endpunkte bauen
// serverseitig DENSELBEN Snapshot (canonical_read.py:249 ruft
// build_paper_portfolio_snapshot_helper und wirft danach alles ausser
// build_exposure_summary(snapshot) weg). Jeder Bau kostet drei Vollpaesse ueber
// ein 5,2-MB-Audit plus einen Marktdaten-Fan-out — auf der Pi mehrere Sekunden.
//
// build_exposure_summary (portfolio_read.py:812) ist nachweislich nur
// snapshot.exposure_summary.to_json_dict() plus generated_at, audit_path,
// available und error — alles Felder, die der Snapshot ohnehin mitliefert. Der
// zweite Endpunkt traegt also keine eigene Information.

const snap = {
  report_type: "paper_portfolio_snapshot",
  generated_at: "2026-09-09T06:30:00+00:00",
  audit_path: "artifacts/paper_execution_audit.jsonl",
  available: true,
  error: null,
  exposure_summary: {
    report_type: "paper_exposure_summary",
    priced_position_count: 6,
    stale_position_count: 1,
    unavailable_price_count: 0,
    gross_exposure_usd: 1843.48,
    net_exposure_usd: 1200.5,
    largest_position_symbol: "BTC/USDT",
    largest_position_weight_pct: 42.5,
    mark_to_market_status: "ok",
    execution_enabled: false,
    write_back_allowed: false,
  },
} as unknown as PortfolioSnapshot;

describe("exposureFromSnapshot", () => {
  it("reproduces what /operator/exposure-summary would have returned", () => {
    const ex = exposureFromSnapshot(snap);
    expect(ex).not.toBeNull();
    expect(ex!.gross_exposure_usd).toBe(1843.48);
    expect(ex!.net_exposure_usd).toBe(1200.5);
    expect(ex!.largest_position_symbol).toBe("BTC/USDT");
    expect(ex!.largest_position_weight_pct).toBe(42.5);
    expect(ex!.priced_position_count).toBe(6);
    expect(ex!.stale_position_count).toBe(1);
    // Die vier Felder, die build_exposure_summary oben draufsetzt.
    expect(ex!.generated_at).toBe("2026-09-09T06:30:00+00:00");
    expect(ex!.audit_path).toBe("artifacts/paper_execution_audit.jsonl");
    expect(ex!.available).toBe(true);
    expect(ex!.error).toBeNull();
  });

  it("returns null when the backend sent no exposure block", () => {
    // Aelteres Backend ohne exposure_summary: lieber ehrlich nichts anzeigen
    // als eine Null-Exposure erfinden, die wie "kein Risiko" aussieht.
    const without = { ...snap, exposure_summary: undefined } as unknown as PortfolioSnapshot;
    expect(exposureFromSnapshot(without)).toBeNull();
  });

  it("carries a backend error through instead of masking it", () => {
    const broken = {
      ...snap,
      available: false,
      error: "audit unreadable",
    } as unknown as PortfolioSnapshot;
    const ex = exposureFromSnapshot(broken);
    expect(ex!.available).toBe(false);
    expect(ex!.error).toBe("audit unreadable");
  });
});
