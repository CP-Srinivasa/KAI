# DECISION_LOG.md

## Current State (2026-03-23)

- current_sprint: `PH4J_FALLBACK_TAGS_ENRICHMENT (candidate)`
- next_required_step: `PH4J_DEFINITION_AND_CONTRACT_FREEZE`
- baseline: `1551 passed, ruff clean`

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

### D-68 (2026-03-23): PH4G execution complete -- relevance floor + I-13 policy blocker
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
- Befund: Companion-ML-Infrastruktur (Sprints 8-15) vorhanden, kein aktives Modell.
- Entscheidung: kein weiterer Ausbau; `[EXPERIMENTAL -- NO ACTIVE MODEL]` markiert.
- Betroffene Module: shadow.py, evaluation.py, distillation.py, training.py, upgrade_cycle.py.
- Aktivierungsvoraussetzungen: trainiertes Model-Artifact + konfigurierter companion_model_endpoint.

### D-72 (2026-03-23): [STRATEGISCH] Signalkern-Ehrlichkeit und Freshness-Enforcement
- Befund: TradingLoop maskierte stale Marktdaten still als `NO_SIGNAL`.
- Entscheidung: expliziter `STALE_DATA` CycleStatus eingefuehrt; stale Daten -> Zyklus-Skip mit WARNING-Log.
- Adapter-Quelle (`market_data_source:<name>`) wird jetzt in jedem Zyklus als Note mitgeschrieben.
- CoinGecko bleibt Default; Mock nur explizit mit WARNING-Log.

### D-73 (2026-03-23): PH4G formal geschlossen; PH4H als aktiver Definition-Sprint eroeffnet
- PH4G closeout recorded; S75 ist jetzt ein frozen intervention anchor.
- PH4H aktiviert in Definition-Modus als aktiver Sprint.
- Zentrale Policy-Frage: `I-13` ceiling vs fallback actionability.
- Next required step: `PH4H_CONTRACT_AND_ACCEPTANCE_FREEZE`.

### D-74 (2026-03-23): [PH4H] Policy-Entscheidung -- actionable ist LLM-exklusiv (Option B)

Kontext: PH4G zeigte, dass I-13 (rule-only priority ceiling <= 5) die actionable-Flagge in
der Fallback-Analyse blockiert. PH4H war ein reiner Review-Sprint -- keine Code-Aenderungen.

Evaluierte Optionen:

| Option | Beschreibung | Risk/Benefit-Fazit |
|---|---|---|
| 1 -- I-13 relaxieren | rule-only priority > 5 erlauben | ABGELEHNT: schwaecht fail-closed; kein semantisches Fundament fuer actionable=True in rule-only |
| 2 / B -- actionable = LLM-only | permanente architektonische Grenze | GEWAEHLT: sauberste Trennung; kein I-13-Drift; fail-closed; System ist paper-only |
| 3 -- Hybrid-Gate | rule-only actionable mit Keyword-Evidenz-Schwellwert | ABGELEHNT: Schwellwert willkuerlich; I-13-Verletzung bleibt; Komplexitaet ohne Nutzen |

Entscheidung: Option B
- `actionable` ist eine semantische Entscheidung und bleibt LLM-exklusiv.
- Rule-only Fallback setzt `actionable=False` immer -- das ist korrekt, nicht ein Bug.
- I-13 bleibt als permanente Invariante aktiv und unveraendert.
- Konsequenz: Fallback-Dokumente haben immer `actionable=False`. Trading-Signale aus Fallback-Pfad
  sind moeglich (via `relevance_score`), werden aber nie als actionable markiert.
- Naechste Hebel: (a) Fallback-Frequenz durch LLM-Durchsatz reduzieren oder
  (b) fehlende Fallback-Felder (market_scope, tags) ohne I-13-Konflikt anreichern.

### D-75 (2026-03-23): PH4H formal geschlossen -- S76 frozen anchor

- PH4H Review-Sprint abgeschlossen; policy-Entscheidung in D-74 dokumentiert.
- I-13 in `docs/intelligence_architecture.md` als confirmed permanent invariant markiert.
- S76 ist ab jetzt immutable frozen anchor.
- Alle Acceptance-Gates erfuellt: policy enumerated; option selected; I-13 updated; PH4I defined; baseline OK.

### D-76 (2026-03-23): PH4I aktiviert -- Relevance/Context-Enrichment als aktiver Definition-Sprint

- Sprint-Kandidat: `PH4I_FALLBACK_MARKET_SCOPE_ENRICHMENT` (candidate; awaitng S77 contract freeze).
- Motivation: PH4F finding `market_scope unknown 69/69` ist policy-safe (kein Scoring-Einfluss, kein I-13-Konflikt).
- Market-Scope-Anreicherung verbessert Operator-Kontext und Dokumentenkategorisierung.
- NÃ¤chster Schritt: `PH4I_CONTRACT_AND_ACCEPTANCE_FREEZE`.

### D-77 (2026-03-23): [PH4I] Contract freeze -- §77 execution-ready

- §77 contract frozen; all definition gates checked.
- Scope confirmed: market_scope enrichment only in `_build_fallback_analysis()`.
- No scoring changes, no I-13 conflict, no actionable changes.
- Acceptance criteria locked: market_scope > 0/69 populated; 1538+ passed; ruff clean.
- PH4I ready for execution (I3).

### D-78 (2026-03-23): PH4I formal geschlossen -- PH4J als naechster Kandidat

- PH4I execution abgeschlossen; 1551 passed (+13 von 1538); ruff clean.
- _fallback_market_scope() enriched: crypto_assets + tickers + title keyword scan.
- market_scope UNKNOWN in fallback wird jetzt bei vorhandenen Asset-Signalen aufgeloest.
- S77 ist ab jetzt geschlossener frozen anchor.
- PH4J_FALLBACK_TAGS_ENRICHMENT als naechster Kandidat definiert (PH4F: tags empty 69/69).
- Neue Baseline: 1551 passed, ruff clean.

