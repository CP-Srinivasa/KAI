# RISK_REGISTER.md

## Current State (2026-03-23)

- current_phase: `PHASE 4 (active)`
- current_sprint: `PH4G_FALLBACK_INPUT_ENRICHMENT_BASELINE (ready to close)`
- next_required_step: `PH4G_CLOSE_AND_PH4H_POLICY_REVIEW`
- baseline: `1538 passed, ruff clean`

---

## Active Phase-4 Risks

| Risk ID | Description | Severity | Likelihood | Mitigation | Status |
|---|---|---|---|---|---|
| R-PH4-010 | Relaxing `I-13` too quickly may weaken fail-closed safety in rule-only mode. | high | medium | Route next step through PH4H policy review before any `I-13` change. | open |
| R-PH4-011 | Keeping `I-13` unchanged may cap Tier-1 usefulness in fallback-heavy scenarios. | medium | medium | Evaluate policy options with explicit risk/benefit evidence in PH4H. | open |
| R-PH4-012 | Repeated fallback interventions without policy clarity may create contradictory outcomes. | high | medium | Freeze policy-first sequence: close PH4G -> PH4H review -> then any intervention. | open |
| R-PH4G-001 | PH4G may become too broad if too many fields are changed at once. | high | medium | Enforce narrow PH4G scope and limit first intervention pass to highest-leverage pathways. | open |
| R-PH4G-002 | Intervention without tight measurement could reduce interpretability. | medium | medium | Require before/after measurements on the same paired set and explicit pathway mapping. | open |

---

## Resolved / Superseded

- PH4B operational blocker (quota) - resolved.
- PH4D regression risk - resolved (`0` regressions).
- PH4D/PH4E governance conflict - resolved.
- PH4E calibration ambiguity - resolved into PH4F diagnostic path.
- PH4F closeout ambiguity - resolved (PH4F formally closed).
- PH4G execution uncertainty - resolved (execution complete; closeout pending).

---

## Refactoring Findings RF-1 .. RF-7 (2026-03-23)

These findings were addressed in a dedicated refactoring session (2026-03-23).

| ID | Titel | Status | Commit |
|---|---|---|---|
| **RF-1** | CLI/MCP monolith split | ✅ implemented | e2949d3, b8c0fad |
| **RF-2** | Working Tree uncommitted | ✅ implemented | f32b147, cbcb34c, dea0ec8 |
| **RF-3** | CORS hardcoded | ✅ implemented (prior) | 4d2cfdd |
| **RF-4** | DB-based aggregation (models + migration) | ✅ partial | 25f84d4 |
| **RF-5** | README/Docs Phase-4 update | ✅ implemented | a089ca7, e86e3aa |
| **RF-6** | CoinGecko default + mock warning | ✅ implemented | faabd6c |
| **RF-7** | Test-file splitting (cli/ + mcp/ submodules) | ✅ implemented | a05f1e7 |

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

Pragmatic complexity audit — see README "Active vs. Experimental Features" table.

| ID | Bereich | Entscheidung | Maßnahme |
|---|---|---|---|
| **CF-1** | Companion ML Pipeline (distillation, training, tuning, upgrade_cycle) | Experimental parken | `[EXPERIMENTAL]` Marker in Modul-Docstrings + CLI-Hilfetext. Kein Modell vorhanden, kein Default-Pfad-Einfluss. Code bleibt als Wiedereinstiegspunkt. |
| **CF-2** | ABCInferenceEnvelope (abc_result.py, route_runner.py) | Experimental dokumentieren | Docstring klärt: nur aktiv in non-primary_only Route-Modi. Production default = primary_only → Modul wird nie aufgerufen. |
| **CF-3** | Inference Route Profile multi-path | Experimental kennzeichnen | inference_profile.py Docstring klärt: production default = primary_only. Multi-path-Modi = experimental, benötigen Companion-Modell. |

### Bewusst NICHT getan (mit Begründung)
- Kein Code-Löschen: ML-Pipeline-Module haben Wiederverwendungswert sobald Modell existiert.
- Kein Event-Sourcing: nicht geplant, nicht vorbereitet.
- Kein Multi-Tenant: nicht geplant.
- Kein Kafka/Message-Queue: nicht geplant.
- Kein DB-Dual-Write jetzt: RF-4 Phase 2 bleibt pending — Risiko > Nutzen zum jetzigen Zeitpunkt.
- Kein weiteres CLI-Splitting: research.py ist groß, aber bereits extrahiert. Weitere Unterteilung bringt jetzt keinen Wartungsgewinn.

---

## Confirmed Context

- PH4E is formally closed.
- PH4F is formally closed and frozen as PH4G intervention anchor.
- Production Tier-1 path is fallback analysis in `app/analysis/pipeline.py` (not `RuleAnalyzer.analyze()`).
- PH4F paired-set findings: actionable missing `69/69`, market_scope unknown `69/69`, tags empty `69/69`, relevance default-floor `56/69`.
- PH4G findings: relevance-floor intervention retained; actionable heuristic reverted due `I-13` ceiling policy.
- Technical baseline unchanged: `1538 passed`, `ruff clean`.
