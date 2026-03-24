# TASKLIST.md

## Current State

- current_phase: `PHASE 5 (active)`
- current_sprint: `PH5C_FILTER_BEFORE_LLM_BASELINE (closed D-97)`
- next_required_step: `STRATEGIC_HOLD -- no new feature-work until alert-hit-rate is calculable on >=50 resolved directional alerts`
- baseline: `1449 passed, ruff clean, mypy 0 errors`
- working_tree: `clean`
- branch: `claude/p6-audit/architectural-invariants`

## Active Tasks

- [ ] Maintain strategic hold: no new companion-ML sprint/decision/invariant before operator lift.
- [ ] Track alert-hit-rate evidence until metric is calculable on >=50 resolved directional alerts.
- [ ] Track paper-trading metrics until a clearly positive finding exists.
- [ ] Enforce documentation policy: no new sprint-contract docs; decisions only via code comments or 3-line DECISION_LOG entries.
- [ ] Monitor Priority MAE and LLM-Error-Proxy as production metrics only (D-101), without opening new internal diagnosis sprints.

## Recently Completed (2026-03-24)

- [x] PH5B findings accepted: 19/19 LLM-error-proxy docs are `EMPTY_MANUAL`.
- [x] Root cause confirmed: empty/manual placeholder content, not model failure.
- [x] PH5B remains closed and PH5C is confirmed as the intended next sprint.

## Closed Phases / Sprints

- [x] Phase 5 / PH5A (D-91) -- Reliability baseline established.
- [x] Phase 5 / PH5B (accepted closeout) -- low-signal cluster root cause identified.
- [x] Phase 4 (D-87, 2026-03-24) -- Signal Quality Calibration, full PH4A-PH4K arc.
- [x] Phase 3 (2026-03-22) -- GO.

## Quality Gate: Alert Hit Rate (D-98)

> **Blocker:** No new feature work until Alert Hit Rate is computable for 50+ resolved directional alerts.

| Prerequisite | Status |
|---|---|
| Structured alert log (alert_id, asset, direction, priority, timestamp) | done (`app/alerts/audit.py` - `AlertAuditRecord`) |
| Outcome annotation store (hit / miss / inconclusive per alert) | done (`app/alerts/audit.py` - `AlertOutcomeAnnotation`, AHR-1) |
| Metric computation - `alerts hit-rate` CLI + `build_outcomes_from_records()` | done (`app/alerts/hit_rate.py`, AHR-1) |
| Operator annotation - `alerts annotate` CLI command | done (`app/cli/main.py`, AHR-1) |
| 50+ resolved directional alerts collected | pending (data collection in progress) |

## Strategic Hold (D-97)

> No new companion-ML sprint, decision, or invariant until alert-hit-rate and paper-trading metrics show clearly positive results.

## Documentation Policy (D-99)

> No new standalone sprint-contract documents. Record decisions as code comments or compact 3-line entries in `DECISION_LOG.md`.

## Known Production Limits (D-101)

> Priority MAE=3.13 and LLM-Error-Proxy=27.5% are accepted production limitations and are improved through operation and real data, not further internal diagnosis sprints.

## Tier1 Fallback Policy (D-104 / I-13)

> `actionable=0` in Tier1/keyword fallback is permanent. Focus remains on LLM-driven alerts with real signal quality, not Tier1 optimisation.
