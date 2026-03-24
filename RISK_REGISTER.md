# RISK_REGISTER.md

## Current State (2026-03-24)

- current_phase: `PHASE 4`
- current_sprint: `PHASE4_CLOSEOUT_AND_NEXT_PHASE_GATE`
- next_required_step: `PHASE4_FINAL_CANONICAL_CLOSEOUT`
- baseline: `1609 passed, ruff clean`
### PH4K Execution Results Note (2026-03-23)

PH4K execution complete; utility artifacts produced. Moving to results review.
- fallback_tags_populated_docs: 69/69 (100%)
- watchlist_overlap_docs: 36/69 (52.17%)
- corr(tag_count, tier3_priority): 0.5564
- mean_tier3_priority with overlap: 5.4444 vs. without: 2.3333 (delta +3.1)
DB test failures remain on a separate track.

### Technical Stabilization Note (2026-03-24)

- PH4A through PH4K are complete enough that PH4L is not urgent.
- V-4 dual-write and DB-primary snapshot closeout are complete (N-4 closed).
- Last explicitly referenced clean governance anchor: commit `c498ca4`.
- Governance conflict remains: mixed wording between "Phase 4 closed" and "closeout gate active".

---

## Active Phase-4 Risks

| Risk ID | Description | Severity | Likelihood | Mitigation | Status |
|---|---|---|---|---|---|
| R-PH4J-001 | Higher tag quantity may not automatically improve operator utility. | medium | medium | Run PH4K utility review before additional enrichment scope. | in_review (PH4K execution evidence is positive; formal closeout review still pending) |
| R-PH4J-002 | Workspace reverts can create closeout confusion if not documented. | low | medium | Keep closeout evidence and revert notes explicit in changelog/decision log. | resolved (D-81: revert noted in changelog/decision log; PH4J formally closed) |
| R-PH4J-003 | DB failures may pollute PH4J interpretation if mixed into same gate. | medium | medium | Keep DB failures on separate track with separate ownership. | resolved (PH4J closed; DB failures remain on separate track; not blocking PH4K) |
| R-PH4-013 | Skipping formal Phase-4 closeout may leave the phase artificially open. | high | medium | Phase 4 formally closed (D-87). | resolved (D-87) |
| R-PH4-014 | Opening Phase 5 too early may weaken phase-boundary clarity and governance traceability. | high | medium | Phase 4 closeout fully synced (D-87); Phase 5 unblocked. | resolved (D-87) |
| R-PH4-010 | Relaxing `I-13` too quickly may weaken fail-closed safety in rule-only mode. | high | medium | Route next step through PH4H policy review before any `I-13` change. | resolved (PH4H D-74: I-13 confirmed permanent; Option B chosen -- no relaxation) |
| R-PH4-011 | Keeping `I-13` unchanged may cap Tier-1 usefulness in fallback-heavy scenarios. | medium | medium | Evaluate policy options with explicit risk/benefit evidence in PH4H. | resolved (PH4H D-74: accepted as architectural constraint; fallback actionable=False by design; next lever = market_scope enrichment in PH4I) |
| R-PH4-012 | Repeated fallback interventions without policy clarity may create contradictory outcomes. | high | medium | Freeze policy-first sequence: close PH4G -> PH4H review -> then any intervention. | resolved (PH4H completed; policy-first sequence executed; PH4I is next policy-safe intervention) |
| R-PH4G-001 | PH4G may become too broad if too many fields are changed at once. | high | medium | Enforce narrow PH4G scope and limit first intervention pass to highest-leverage pathways. | resolved (PH4G formally closed; S75 immutable anchor confirmed; scope held to 1 retained + 1 reverted intervention) |
| R-PH4G-002 | Intervention without tight measurement could reduce interpretability. | medium | medium | Require before/after measurements on the same paired set and explicit pathway mapping. | resolved (PH4G execution produced clear before/after evidence; formal closeout recorded) |

---

## Resolved / Superseded

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
| **RF-8** | research.py God-File (3424 Zeilen, 57 Commands) | implemented | 995eb3a — split into 4 Submodule |
| **RF-9** | API auth guard fehlt in production | implemented | 144da3c — validate_secrets fail-fast |
| **RF-10** | IdempotencyStore / RateLimitStore inline | implemented | 3262abb — Store-Klassen mit Single-Instance-Warnung |
| **RF-11** | Property-Based Tests Risk-Engine fehlten | implemented | 3f69cf8 — 7 Hypothesis-Invarianten |
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
- PH4J findings: fallback tags enriched with categories, affected_assets, source_name, market_scope.value; keyword-hit 4→7, zero-hit 1→4, assets-only 0→4; PH4J formally closed (D-80); §78 frozen anchor.
- PH4K execution (D-81): fallback_tags_populated 69/69; watchlist_overlap 36/69 (52.17%); corr(tag_count, tier3_priority)=0.5564; mean_priority with overlap 5.44 vs. without 2.33; utility signal observed, closeout review pending.
- Technical baseline: `1609 passed`, `ruff clean`.
