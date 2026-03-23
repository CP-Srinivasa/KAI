# KNOWLEDGE_BASE.md - KAI Canonical Knowledge Index

> Stand: 2026-03-23 | Phase 4 active | Sprint: PH4E_SCORING_CALIBRATION_AUDIT | Next: PH4E_EXECUTION_START | Baseline: 1519 passed, ruff clean

## PH4A-PH4D Arc (frozen evidence)

- PH4A: baseline established
- PH4B: overlap restored (`paired_count=69`, tier3 coverage `100.0%`)
- PH4C: rule-keyword gaps diagnosed
- PH4D: targeted keyword expansion improved hit quality without regressions

## PH4D Outcome Snapshot

- zero-hit: `29 -> 26`
- low-hit: `27 -> 25`
- good-hit: `13 -> 18`
- remaining zero-hit docs: `26`
  - true gaps: `5`
  - low-value noise: `21`

## PH4E Freeze Snapshot

- PH4E scope is frozen to scoring divergence diagnostics on the 69 paired documents.
- PH4E remains strictly diagnostic-only.
- No scoring/threshold/rule/provider/source/model/runtime changes are allowed.

## Active Gate

- current sprint: `PH4E_SCORING_CALIBRATION_AUDIT`
- next required step: `PH4E_EXECUTION_START`
