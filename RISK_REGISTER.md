# RISK_REGISTER.md

## Current State (2026-03-23)

- current_phase: `PHASE 4 (active)`
- current_sprint: `PH4H_RULE_ONLY_CEILING_AND_ACTIONABILITY_POLICY_REVIEW (active definition)`
- next_required_step: `PH4H_CONTRACT_AND_ACCEPTANCE_FREEZE`
- baseline: `1538 passed, ruff clean`

---

## Active Phase-4 Risks

| Risk ID | Description | Severity | Likelihood | Mitigation | Status |
|---|---|---|---|---|---|
| R-PH4-010 | Relaxing `I-13` too quickly may weaken fail-closed safety in rule-only mode. | high | medium | Route next step through PH4H policy review before any `I-13` change. | open |
| R-PH4-011 | Keeping `I-13` unchanged may cap Tier-1 usefulness in fallback-heavy scenarios. | medium | medium | Evaluate policy options with explicit risk/benefit evidence in PH4H. | open |
| R-PH4-012 | Repeated fallback interventions without policy clarity may create contradictory outcomes. | high | medium | Freeze policy-first sequence: close PH4G -> PH4H review -> then any intervention. | open |
| R-PH4G-001 | PH4G may become too broad if too many fields are changed at once. | high | medium | Enforce narrow PH4G scope and limit first intervention pass to highest-leverage pathways. | resolved (PH4G closed; scope held to 1 retained + 1 reverted intervention) |
| R-PH4G-002 | Intervention without tight measurement could reduce interpretability. | medium | medium | Require before/after measurements on the same paired set and explicit pathway mapping. | resolved (PH4G execution produced clear before/after evidence; closeout recorded) |

---

## Refactoring Findings RF-1 .. RF-7 (2026-03-23)

These findings were addressed in a dedicated refactoring session (2026-03-23).

| ID | Titel | Status | Commit |
|---|---|---|---|
| **RF-1** | CLI/MCP monolith split | âœ… implemented | e2949d3, b8c0fad |
| **RF-2** | Working Tree uncommitted | âœ… implemented | f32b147, cbcb34c, dea0ec8 |
| **RF-3** | CORS hardcoded | âœ… implemented (prior) | 4d2cfdd |
| **RF-4** | DB-based aggregation (models + migration) | âœ… partial | 25f84d4 |
| **RF-5** | README/Docs Phase-4 update | âœ… implemented | a089ca7, e86e3aa |
| **RF-6** | CoinGecko default + mock warning | âœ… implemented | faabd6c |
| **RF-7** | Test-file splitting (cli/ + mcp/ submodules) | âœ… implemented | a05f1e7 |

### RF-1 Detail
- `app/cli/commands/trading.py`: new `trading_app` with market-data, paper-portfolio, trading-loop, backtest, decision-journal commands
- `app/cli/research.py`: research commands fully extracted from main.py
- `app/agents/tools/canonical_read.py` + `guarded_write.py`: MCP tool inventory modules
- Backward-compatible: all `trading-bot research <cmd>` commands unchanged

### RF-4 Detail (partial)
Phase 1 complete: ORM models + Alembic migration (0007).
Phase 2 (dual-write in run_cycle) and Phase 3 (DB-primary portfolio snapshot) are pending sprints.

---

## Complexity Findings CF-1 .. CF-3 (2026-03-23)

Pragmatic complexity audit â€” see README "Active vs. Experimental Features" table.

| ID | Bereich | Entscheidung | MaÃŸnahme |
|---|---|---|---|
| **CF-1** | Companion ML Pipeline (distillation, training, tuning, upgrade_cycle) | Experimental parken | `[EXPERIMENTAL]` Marker in Modul-Docstrings + CLI-Hilfetext. Kein Modell vorhanden, kein Default-Pfad-Einfluss. Code bleibt als Wiedereinstiegspunkt. |
| **CF-2** | ABCInferenceEnvelope (abc_result.py, route_runner.py) | Experimental dokumentieren | Docstring klÃ¤rt: nur aktiv in non-primary_only Route-Modi. Production default = primary_only â†’ Modul wird nie aufgerufen. |
| **CF-3** | Inference Route Profile multi-path | Experimental kennzeichnen | inference_profile.py Docstring klÃ¤rt: production default = primary_only. Multi-path-Modi = experimental, benÃ¶tigen Companion-Modell. |

### Bewusst NICHT getan (mit BegrÃ¼ndung)
- Kein Code-LÃ¶schen: ML-Pipeline-Module haben Wiederverwendungswert sobald Modell existiert.
- Kein Event-Sourcing: nicht geplant, nicht vorbereitet.
- Kein Multi-Tenant: nicht geplant.
- Kein Kafka/Message-Queue: nicht geplant.
- Kein DB-Dual-Write jetzt: RF-4 Phase 2 bleibt pending â€” Risiko > Nutzen zum jetzigen Zeitpunkt.
- Kein weiteres CLI-Splitting: research.py ist groÃŸ, aber bereits extrahiert. Weitere Unterteilung bringt jetzt keinen Wartungsgewinn.

---

## Strategic Alignment Audit (2026-03-23)

| ID | Bereich | Befund | MaÃŸnahme | Status |
|---|---|---|---|---|
| **SA-1** | Companion-ML-Infrastruktur | Vorhanden (Sprints 8â€“15), kein aktives Modell, kein kurzfristiger Aktivierungsplan. Infrastruktur zu frÃ¼h fÃ¼r Produktivbetrieb. | Als `[EXPERIMENTAL â€” NO ACTIVE MODEL]` markiert in `shadow.py`, `evaluation.py` (neu). Distillation/training/upgrade_cycle bereits markiert. Kein Ausbau bis Aktivierungsvoraussetzungen erfÃ¼llt. | âœ… D-71 |
| **SA-2** | Signalkern Freshness-Enforcement | TradingLoop maskierte stale Daten still als `NO_SIGNAL`. Adapter-Quelle war im Audit nicht sichtbar. | Expliziter `STALE_DATA` CycleStatus eingefÃ¼hrt. Stale â†’ Zyklus-Skip mit WARNING-Log. Adapter-Quelle in Notes/Audit. | âœ… D-72 |
| **SA-3** | Signalkern Strategie-Transparenz | SignalGenerator leitet Richtung ausschlieÃŸlich aus LLM-Sentiment ab â€” kein technischer Indikator, kein Orderbook. Dieses Risiko war undokumentiert. | Docstring in `signals/generator.py` klÃ¤rt aktuellen Stand und TODO vor Live-Einsatz. Keine neue Logik. | âœ… D-72 |

---

## Confirmed Context

- PH4E is formally closed.
- PH4F is formally closed and frozen as PH4G intervention anchor.
- Production Tier-1 path is fallback analysis in `app/analysis/pipeline.py` (not `RuleAnalyzer.analyze()`).
- PH4F paired-set findings: actionable missing `69/69`, market_scope unknown `69/69`, tags empty `69/69`, relevance default-floor `56/69`.
- PH4G findings: relevance-floor intervention retained; actionable heuristic reverted due `I-13` ceiling policy.
- Technical baseline unchanged: `1538 passed`, `ruff clean`.
