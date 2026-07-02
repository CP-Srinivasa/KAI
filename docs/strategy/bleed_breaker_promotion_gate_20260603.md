# Bleed-Breaker — Promotion / Re-Enable Gate (2026-06-03)

Enforcement-Hälfte von Pre-Re-Enable-Blocker #4. Die Detektion
(`app/observability/position_risk.py`, CLI `trading positions-risk-snapshot`)
*sieht* einen offenen Positions-Bleed; dieser Gate *blockiert* eine
risiko-erhöhende `EntryMode`-Hochstufung, solange der Bleed (oder unbekannte
Positionsdaten) ungelöst ist.

## Semantik — Promotion-Stop, KEIN Trading-Stop

`app/risk/promotion_gate.py` → `evaluate_promotion(current, target, risk_report)`.

**Immer erlaubt (nie gegated):** read-only Diagnostik, Positions-Snapshot,
Risk-Report, **Exits, Risk-Reductions, De-Risking** (Ziel-Rang ≤ aktueller Rang,
z.B. `probe → disabled`), laterale Transitions.

**Blockiert → `manual_review_required`:** risiko-erhöhende Transitions entlang der
Ladder `disabled → paper → probe → live_limited → live_normal`, wenn eine offene
Position `risk_open`/`data_unknown`/source-stale ist, der aggregierte unrealisierte
PnL blutet, oder das Positions-Risk-Artefakt fehlt.

## Fail-closed (SENTR-Posture)

Bei fehlendem Artefakt, nicht verfügbarem Snapshot, `data_unknown` oder stale
Source **blockiert** der Gate (statt durchzuwinken). Er schließt nie automatisch
(`auto_close` ist eine separate, bewusst nicht gebaute Policy) und ändert nie den
Execution-State — er liefert nur eine Entscheidung, die der Aufrufer honorieren muss.

## Reason-Codes

| Code | Bedeutung |
|---|---|
| `PROMOTION_BLOCKED_RISK_OPEN` | offene Position jenseits Verlust-Schwelle |
| `PROMOTION_BLOCKED_POSITION_DATA_UNKNOWN` | Preis/Status nicht bewertbar |
| `PROMOTION_BLOCKED_POSITION_SOURCE_STALE` | stale/fehlende Marktdaten-Source |
| `PROMOTION_BLOCKED_UNREALIZED_BLEED` | aggregierter unrealisierter Verlust ≤ −Schwelle |
| `PROMOTION_BLOCKED_MISSING_ARTIFACT` | kein Positions-Risk-Artefakt (fail-closed) |

## CLI

```
# Blockt risiko-erhöhende Promotion fail-closed (exit 1) bei offenem Bleed:
python -m app.cli.main trading promotion-check --target paper --current disabled

# De-Risking ist nie gegated (exit 0):
python -m app.cli.main trading promotion-check --target disabled --current paper

# Optional: aggregierte Bleed-Schwelle (USD) + persistiertes Artefakt:
python -m app.cli.main trading promotion-check --target probe --bleed-usd-threshold 50 \
  --out artifacts/promotion_gate_decision.json
```

Exit-Code: `1` = blockiert (manual_review_required), `0` = erlaubt. `--current`
defaultet auf das konfigurierte `EXECUTION_ENTRY_MODE`.

## Grenzen / Was dieser Gate NICHT tut

- Ersetzt **nicht** das Edge-Release-Gate (`trading edge-gate`) oder den
  Operator-Sign-off — `allowed=True` heißt nur „kein offener Positions-Bleed",
  nicht „Edge nachgewiesen".
- Greift **nicht** automatisch in `trading_loop`/Exits ein.
- Promotet/demotet `entry_mode` nicht selbst — `EXECUTION_ENTRY_MODE` bleibt eine
  explizite Operator-Aktion.
