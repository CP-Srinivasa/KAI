# RUNBOOK.md

## Operator-Basisablauf

1. Prüfe `research readiness-summary`
2. Prüfe `research gate-summary`
3. Prüfe `research decision-pack-summary`
4. Prüfe `research operator-runbook`
5. Falls nötig: `research review-journal-append`
6. Bei blockierenden Risiken: `/pause` oder `/kill`

## Wichtige Surfaces

- CLI:
  - `research readiness-summary`
  - `research gate-summary`
  - `research decision-pack-summary`
  - `research operator-runbook`
  - `research review-journal-summary`
  - `research resolution-summary`
- MCP:
  - `get_operational_readiness_summary`
  - `get_protective_gate_summary`
  - `get_decision_pack_summary`
  - `get_operator_runbook`
  - `get_review_journal_summary`
  - `get_resolution_summary`

## Notfallregeln

- bei Unsicherheit: keine Ausführung
- bei Risk- oder Dateninkonsistenz: Zustand einfrieren und Incident dokumentieren
- bei bestätigtem Notfall: `/kill` doppelt bestätigen

## Betriebsmodi

- `research`: Analyse ohne Ausführung
- `backtest`: historische Auswertung
- `paper`: simulierte Ausführung
- `shadow`: parallele Bewertung ohne Orders
- `live`: nur nach expliziter Freigabe und bestandenen Gates
