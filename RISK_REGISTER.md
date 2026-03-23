# RISK_REGISTER.md

## Current State (2026-03-23)

- current_phase: `PHASE 4 (active)`
- current_sprint: `PH4I_FALLBACK_MARKET_SCOPE_ENRICHMENT (active definition)`
- next_required_step: `PH4I_EXECUTION`
- baseline: `1538 passed, ruff clean`

---

## Active Phase-4 Risks

| Risk ID | Description | Severity | Likelihood | Mitigation | Status |
|---|---|---|---|---|---|
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

## Refactoring Findings RF-1 .. RF-7 (2026-03-23)

These findings were addressed in a dedicated refactoring session (2026-03-23).

| ID | Titel | Status | Commit |
|---|---|---|---|
| **RF-1** | CLI/MCP monolith split | implemented | e2949d3, b8c0fad |
| **RF-2** | Working Tree uncommitted | implemented | f32b147, cbcb34c, dea0ec8 |
| **RF-3** | CORS hardcoded | implemented (prior) | 4d2cfdd |
| **RF-4** | DB-based aggregation (models + migration) | partial | 25f84d4 |
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
- Technical baseline unchanged: `1538 passed`, `ruff clean`.
