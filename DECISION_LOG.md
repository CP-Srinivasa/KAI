# DECISION_LOG.md

## Current State (2026-03-23)

- current_sprint: `PH4G_FALLBACK_INPUT_ENRICHMENT_BASELINE (ready to close)`
- next_required_step: `PH4G_CLOSE_AND_PH4H_POLICY_REVIEW`
- baseline: `1538 passed, ruff clean`

## Canonical Decisions

### D-53: PH4A closed
### D-54-D-56: PH4B definition/freeze/provider
### D-57: PH4B passed (paired=69, MAE=3.13)
### D-58-D-59: Review-first, root cause keyword blindness
### D-60: PH4B closed, PH4C opened
### D-61: PH4C complete (42% zero-hit)
### D-62: PH4C closed, PH4D opened
### D-63: PH4D complete (56 keywords, 42%->37.7%)
### D-64: PH4D closed, diminishing returns
### D-65: PH4E opened (scoring calibration)
### D-66: PH4E complete (defaults by design)
### D-67: PH4F complete (fallback path and input-completeness anchor)

### D-68 (2026-03-23): PH4G execution complete — relevance floor + I-13 policy blocker
- Intervention 1 (relevance floor): applied successfully.
- Intervention 2 (actionable heuristic): reverted due `I-13` rule-only ceiling (`priority <= 5`).
- Consequence: further fallback usefulness is now policy-constrained, not purely implementation-constrained.

### D-69 (2026-03-23): PH4G moved to ready-to-close state
- PH4G remains the active sprint until formal closeout is recorded.
- Retained: relevance-floor fallback intervention.
- Reverted: actionable heuristic (blocked by policy, not by implementation defect).
- Baseline reconfirmed: `1538 passed`, `ruff clean`.

### D-70 (2026-03-23): PH4H selected as next sprint candidate
- Recommended next sprint: `PH4H_RULE_ONLY_CEILING_AND_ACTIONABILITY_POLICY_REVIEW`.
- Constraint: no direct `I-13` change before PH4H policy review.
- Next required step: `PH4G_CLOSE_AND_PH4H_POLICY_REVIEW`.

### D-71 (2026-03-23): [STRATEGISCH] Companion-ML-Infrastruktur als EXPERIMENTAL geparkt
- Befund: Companion-ML-Infrastruktur (Sprints 8–15) vorhanden, kein aktives Modell, kein kurzfristiger Aktivierungsplan.
- Entscheidung: kein weiterer Ausbau; aus Default-Narrativ herausgenommen; als `[EXPERIMENTAL — NO ACTIVE MODEL]` markiert.
- Betroffene Module: `app/research/shadow.py`, `app/research/evaluation.py` (EXPERIMENTAL-Marker ergänzt); `app/research/distillation.py`, `app/research/training.py`, `app/research/upgrade_cycle.py` bereits markiert.
- Companion-CLI-Commands (`benchmark-companion`, `check-promotion`, `record-promotion`) bleiben erhalten, sind bereits als `[EXPERIMENTAL]` deklariert.
- MCP-Tool `get_upgrade_cycle_status` bleibt erhalten (read-only, kein Default-Pfad-Impact).
- Aktivierungsvoraussetzungen: trainiertes Model-Artifact + konfigurierter `companion_model_endpoint` + validierter Promotion-Workflow.

### D-72 (2026-03-23): [STRATEGISCH] Signalkern-Ehrlichkeit und Freshness-Enforcement
- Befund: TradingLoop maskierte stale Marktdaten still als `NO_SIGNAL`; Adapter-Quelle war im Audit nicht sichtbar.
- Entscheidung: expliziter `STALE_DATA` CycleStatus eingeführt; stale Daten → Zyklus-Skip mit WARNING-Log + Audit-Eintrag.
- Adapter-Quelle (`market_data_source:<name>`) wird jetzt in jedem Zyklus als Note mitgeschrieben.
- CoinGecko bleibt Default (`APP_MARKET_DATA_PROVIDER=coingecko`); Mock nur explizit, mit WARNING-Log.
- Signal-Generator: Docstring klargestellt — Richtung aus LLM-Sentiment, keine technischen Indikatoren (TODO vor Live-Einsatz).
- Keine neue Strategie-Logik eingebaut — ehrlicher machen, nicht überbauen.
