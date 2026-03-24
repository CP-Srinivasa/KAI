# RISK_REGISTER.md


## Current State (2026-03-24)


- current_phase: `PHASE 5 (active)`


- current_sprint: `PH5C_FILTER_BEFORE_LLM_BASELINE (closed D-97)`


- next_required_step: `STRATEGIC_HOLD -- no new sprint until alert-precision + paper-trading positive`


- baseline: `1449 passed, ruff clean, mypy 0 errors`


### PH4K Execution Results Note (2026-03-23)


PH4K execution complete; utility artifacts produced. Moving to results review.


- fallback_tags_populated_docs: 69/69 (100%)


- watchlist_overlap_docs: 36/69 (52.17%)


- corr(tag_count, tier3_priority): 0.5564


- mean_tier3_priority with overlap: 5.4444 vs. without: 2.3333 (delta +3.1)


DB test failures remain on a separate track.


### PH5B Cluster Findings Note (2026-03-24)


- PH5B execution has completed and cluster artifacts are available for review.


- All 19 LLM-error-proxy cases belong to the `EMPTY_MANUAL` cluster.


- Root cause is empty/manual placeholder content, not model failure.


- Status report exists in-repo (`status_report.md`).


- Working tree is clean.


- Baseline remains `1609 passed` and `ruff clean`.


---


## Active Phase-5 Risks

| Risk ID | Description | Severity | Likelihood | Mitigation | Status |
|---|---|---|---|---|---|
| R-PH5-001 | Rolling back to PH5B would create artificial governance churn. | high | medium | Keep PH5B closed; strategic hold (D-97) is canonical top state. | closed (D-97) |

---

## Phase-4 Risk Archive


| Risk ID | Description | Severity | Likelihood | Mitigation | Status |


|---`r`n`r`n## Resolved / Superseded


- PH4B operational blocker (quota) - resolved.


- PH4D regression risk - resolved (`0` regressions).


- PH4D/PH4E governance conflict - resolved.


- PH4E calibration ambiguity - resolved into PH4F diagnostic path.


- PH4F closeout ambiguity - resolved (PH4F formally closed).


- PH4G execution uncertainty - resolved (execution complete; PH4G closed).


- R-PH4-010..012 - resolved (PH4H policy decision D-74: I-13 permanent; actionable=LLM-only).


---


## Refactoring Findings RF-8 .. RF-12 (Phase 4H Remediation, 2026-03-23)


| ID | Bereich | Status | Commit |


|---|---|---|---|


| **RF-12** | Circular-import-Risiko nach CLI-Split | mitigated | lazy import in research_operator.py |


---


## Refactoring Findings RF-1 .. RF-7 (2026-03-23)


These findings were addressed in a dedicated refactoring session (2026-03-23).


| ID | Titel | Status | Commit |


|---|---|---|---|


| **RF-1** | CLI/MCP monolith split | implemented | e2949d3, b8c0fad |


| **RF-2** | Working Tree uncommitted | implemented | f32b147, cbcb34c, dea0ec8 |


| **RF-3** | CORS hardcoded | implemented (prior) | 4d2cfdd |


| **RF-4** | DB-based aggregation (models + migration) | phase-3-complete | 25f84d4, V-4-P3 |


| **RF-5** | README/Docs Phase-4 update | implemented | a089ca7, e86e3aa |


| **RF-6** | CoinGecko default + mock warning | implemented | faabd6c |


| **RF-7** | Test-file splitting (cli/ + mcp/ submodules) | implemented | a05f1e7 |


---


## Complexity Findings CF-1 .. CF-3 (2026-03-23)


| ID | Bereich | Entscheidung | Massnahme |


|---|---|---|---|


| **CF-1** | Companion ML Pipeline | Experimental parken | `[EXPERIMENTAL]` Marker in Modul-Docstrings + CLI-Hilfetext. |


| **CF-2** | ABCInferenceEnvelope | Experimental dokumentieren | Docstring klaert: nur aktiv in non-primary_only Route-Modi. |


| **CF-3** | Inference Route Profile multi-path | Experimental kennzeichnen | inference_profile.py Docstring klaert: production default = primary_only. |


---


## Confirmed Context


- PH4E is formally closed.


- PH4F is formally closed and frozen as PH4G intervention anchor.


- PH4G is formally closed and frozen as S75 immutable anchor.


- PH4H is formally closed (D-74/75); policy decision: actionable=LLM-only; I-13 permanent.


- Production Tier-1 path is fallback analysis in `app/analysis/pipeline.py`.


- PH4F paired-set findings: actionable missing `69/69`, market_scope unknown `69/69`, tags empty `69/69`, relevance default-floor `56/69`.


- PH4G findings: relevance-floor retained; actionable reverted (I-13 ceiling policy).


- PH4H findings: I-13 confirmed permanent; actionable=False in fallback is correct by design.


- PH4I findings: _fallback_market_scope enriched; market_scope resolved for docs with crypto_assets/tickers/title keywords.




- Technical baseline: `1609 passed`, `ruff clean`.


