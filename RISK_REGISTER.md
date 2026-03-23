# RISK_REGISTER.md

## Current State (2026-03-23)

- current_phase: `PHASE 4 (active)`
- current_sprint: `PH4E_SCORING_CALIBRATION_AUDIT`
- next_required_step: `PH4E_EXECUTION_START`
- baseline: `1519 passed, ruff clean`

## Active Risks

| Risk ID | Description | Severity | Likelihood | Mitigation | Status |
|---|---|---|---|---|---|
| R-PH4-010 | PH4E execution could drift from diagnostics into intervention. | high | medium | Enforce frozen non-goals in PH4E contract and keep execution audit-only. | open |
| R-PH4-011 | PH4E acceptance interpretation could re-broaden scope after freeze. | high | medium | Keep acceptance criteria explicit and narrow in §73; reject out-of-scope outputs. | open |
| R-PH4-012 | Divergence analysis could over-trust Tier-3 without contextual review. | medium | medium | Require root-cause classification (defaults/calibration/missing signal) before any follow-up sprint. | open |

## Resolved / Superseded

- PH4B operational blocker (quota) - resolved.
- PH4D regression risk - resolved (`0` regressions).
- PH4D/PH4E governance conflict - resolved.
- PH4E pre-freeze governance ambiguity - resolved by contract freeze.

## Confirmed Context

- PH4D metrics: zero-hit `29 -> 26`, low-hit `27 -> 25`, good-hit `13 -> 18`.
- Remaining zero-hit docs: `26` (`5` true gaps, `21` low-value noise).
- Technical baseline unchanged: `1519 passed`, `ruff clean`.
