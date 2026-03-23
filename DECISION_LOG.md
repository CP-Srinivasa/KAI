# DECISION_LOG.md

## Current State (2026-03-23)

- current_sprint: `PH4H_RULE_ONLY_CEILING_AND_ACTIONABILITY_POLICY_REVIEW (active definition)`
- next_required_step: `PH4H_CONTRACT_AND_ACCEPTANCE_FREEZE`
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

### D-68 (2026-03-23): PH4G execution complete â€” relevance floor + I-13 policy blocker
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

### D-73 (2026-03-23): PH4G formally closed; PH4H opened as active definition sprint
- PH4G closeout recorded; Â§75 is now a frozen intervention anchor.
- PH4H activated in definition mode as the active sprint.
- Central policy question confirmed: `I-13` ceiling vs fallback actionability.
- Next required step set to `PH4H_CONTRACT_AND_ACCEPTANCE_FREEZE`.

### D-71 (2026-03-23): [STRATEGISCH] Companion-ML-Infrastruktur als EXPERIMENTAL geparkt
- Befund: Companion-ML-Infrastruktur (Sprints 8â€“15) vorhanden, kein aktives Modell, kein kurzfristiger Aktivierungsplan.
- Entscheidung: kein weiterer Ausbau; aus Default-Narrativ herausgenommen; als `[EXPERIMENTAL â€” NO ACTIVE MODEL]` markiert.
- Betroffene Module: `app/research/shadow.py`, `app/research/evaluation.py` (EXPERIMENTAL-Marker ergÃ¤nzt); `app/research/distillation.py`, `app/research/training.py`, `app/research/upgrade_cycle.py` bereits markiert.
- Companion-CLI-Commands (`benchmark-companion`, `check-promotion`, `record-promotion`) bleiben erhalten, sind bereits als `[EXPERIMENTAL]` deklariert.
- MCP-Tool `get_upgrade_cycle_status` bleibt erhalten (read-only, kein Default-Pfad-Impact).
- Aktivierungsvoraussetzungen: trainiertes Model-Artifact + konfigurierter `companion_model_endpoint` + validierter Promotion-Workflow.

### D-72 (2026-03-23): [STRATEGISCH] Signalkern-Ehrlichkeit und Freshness-Enforcement
- Befund: TradingLoop maskierte stale Marktdaten still als `NO_SIGNAL`; Adapter-Quelle war im Audit nicht sichtbar.
- Entscheidung: expliziter `STALE_DATA` CycleStatus eingefÃ¼hrt; stale Daten â†’ Zyklus-Skip mit WARNING-Log + Audit-Eintrag.
- Adapter-Quelle (`market_data_source:<name>`) wird jetzt in jedem Zyklus als Note mitgeschrieben.
- CoinGecko bleibt Default (`APP_MARKET_DATA_PROVIDER=coingecko`); Mock nur explizit, mit WARNING-Log.
- Signal-Generator: Docstring klargestellt â€” Richtung aus LLM-Sentiment, keine technischen Indikatoren (TODO vor Live-Einsatz).
- Keine neue Strategie-Logik eingebaut â€” ehrlicher machen, nicht Ã¼berbauen.

### D-73A (2026-03-23): [superseded draft] PH4H policy pre-analysis (not canonical)

- Status: superseded draft; not canonical until PH4H contract/acceptance freeze.

**Kontext:** PH4G zeigte, dass `I-13` (rule-only priority ceiling â‰¤ 5) die `actionable`-Flagge in der
Fallback-Analyse blockiert. PH4H is currently in definition mode; review execution is not yet frozen.

**Evaluierte Optionen:**

| Option | Beschreibung | Risk/Benefit-Fazit |
|---|---|---|
| 1 â€” I-13 relaxieren | rule-only priority > 5 unter Bedingungen erlauben | ABGELEHNT: schwÃ¤cht fail-closed Garantie; `actionable=True` ohne LLM hat keine semantische Grundlage; kein Test-Fundament fÃ¼r Grenzwert-Wahl |
| 2 â€” actionable = LLM-only | als permanente architektonische Grenze dokumentieren | **GEWÃ„HLT**: sauberste Trennung; kein I-13-Drift; konsistent mit fail-closed; System ist paper-only |
| 3 â€” Hybrid-Gate | rule-only actionable nur mit explizitem Keyword-Evidenz-Schwellwert | ABGELEHNT: Schwellwert ohne Evidenz willkÃ¼rlich; I-13-Verletzung bleibt; mehr KomplexitÃ¤t ohne Nutzen |

**Preliminary recommendation (not frozen): Option 2**
- `actionable` ist eine semantische Entscheidung und bleibt LLM-exklusiv.
- Rule-only Fallback setzt `actionable=False` immer â€” das ist architektonisch korrekt, nicht ein Bug.
- I-13 bleibt als permanente Invariante aktiv und unverÃ¤ndert.
- Konsequenz: Fallback-Dokumente haben immer `actionable=False`. Trading-Signale aus Fallback-Pfad sind mÃ¶glich (via `relevance_score`), werden aber nie als actionable markiert.
- NÃ¤chste Hebel: (a) Fallback-Frequenz durch LLM-Durchsatz-Optimierung reduzieren oder (b) fehlende Fallback-Felder (`market_scope`, `tags`) ohne I-13-Konflikt anreichern.


### D-74 (2026-03-23): [superseded draft] PH4H closeout + PH4I opening

- This transition was drafted prematurely and is not canonical for the current governance state.
- Canonical state remains:
  - `current_sprint = PH4H_RULE_ONLY_CEILING_AND_ACTIONABILITY_POLICY_REVIEW (active definition)`
  - `next_required_step = PH4H_CONTRACT_AND_ACCEPTANCE_FREEZE`
- PH4I is not active in this state and stays outside the current sprint scope.


