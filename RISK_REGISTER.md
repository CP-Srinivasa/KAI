# RISK_REGISTER.md

## Current State (2026-03-23)

- current_phase: `PHASE 4 (active)`
- current_sprint: `PH4F_RULE_INPUT_COMPLETENESS_AUDIT (ready to close)`
- next_required_step: `PH4F_RESULTS_REVIEW_AND_PH4G_SELECTION`
- baseline: `1519 passed, ruff clean`

---

## Active Phase-4 Risks

| Risk ID | Description | Severity | Likelihood | Mitigation | Status |
|---|---|---|---|---|---|
| R-PH4-010 | PH4F may drift from input-completeness diagnostics into direct rule reform. | high | medium | Keep PH4F diagnostic-only and block intervention edits before closeout review. | open |
| R-PH4-011 | PH4F input analysis may become too broad without strict field separation. | high | medium | Keep outputs field-separated and prioritize only top pathways for closeout. | open |
| R-PH4-012 | Root-cause confidence may be overstated without paired-set evidence trace. | medium | medium | Keep evidence locked to the frozen 69 paired documents and per-field counters. | open |
| R-PH4G-001 | PH4G may become too broad if too many fields are changed at once. | high | medium | Enforce narrow PH4G scope and limit first intervention pass to highest-leverage pathways. | open |
| R-PH4G-002 | Intervention without tight measurement could reduce interpretability. | medium | medium | Require before/after measurements on the same paired set and explicit pathway mapping. | open |

---

## Resolved / Superseded

- PH4B operational blocker (quota) - resolved.
- PH4D regression risk - resolved (`0` regressions).
- PH4D/PH4E governance conflict - resolved.
- PH4E calibration ambiguity - resolved into PH4F diagnostic path.

---

## Confirmed Context

- PH4E is formally closed.
- PH4F execution is complete and in review/closeout mode.
- Production Tier-1 path is fallback analysis in `app/analysis/pipeline.py` (not `RuleAnalyzer.analyze()`).
- PH4F paired-set findings: actionable missing `69/69`, market_scope unknown `69/69`, tags empty `69/69`, relevance default-floor `56/69`.
- Technical baseline unchanged: `1519 passed`, `ruff clean`.
