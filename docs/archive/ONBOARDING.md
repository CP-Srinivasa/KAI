# ONBOARDING.md

## Purpose

Operator onboarding baseline after formal Phase-2 closure.
Current sprint: `S50_CANONICAL_CONSOLIDATION_BASELINE` (Phase 3).
Next active sprint: `S50B_PROVISIONAL_CLI_GOVERNANCE`.
S50 focus is consolidation and clarity, not feature expansion.

## 1. Safety Baseline

- default mode: `paper` or `shadow`
- `live` stays disabled by default
- no unvalidated model output on critical paths
- fail closed on uncertainty

## 2. Verify Environment

```bash
pip install -e ".[dev]"
python -m pytest
python -m ruff check .
```

Expected reference: `1519 passed`, `ruff clean`.

## 3. Core Read Surfaces

```bash
research daily-summary
research readiness-summary
research decision-pack-summary
research paper-positions-summary
research paper-exposure-summary
research trading-loop-status
research trading-loop-recent-cycles
research review-journal-summary
research resolution-summary
research alert-audit-summary
```

## 4. Dashboard and API

Dashboard:

```text
http://localhost:8000/dashboard
```

Read-only API endpoints:

- `/operator/status`
- `/operator/readiness`
- `/operator/decision-pack`
- `/operator/daily-summary`
- `/operator/portfolio-snapshot`
- `/operator/exposure-summary`
- `/operator/trading-loop/status`
- `/operator/trading-loop/recent-cycles`
- `/operator/review-journal`
- `/operator/resolution-summary`
- `/operator/alert-audit`

Guarded endpoint:

- `POST /operator/trading-loop/run-once` (paper/shadow only)

## 5. Telegram Commands

- read-only: `/status`, `/health`, `/positions`, `/exposure`, `/risk`, `/signals`, `/journal`, `/resolution`, `/decision_pack`, `/daily_summary`, `/alert_status`
- audit-only: `/approve <decision_ref>`, `/reject <decision_ref>`, `/incident <note>`
- guarded controls: `/pause`, `/resume`, `/kill` (with confirmation)

## 6. Closeout Result

- API: PASS
- Dashboard: PASS
- Telegram: PASS
- CLI baseline + drilldown: PASS
- Go/No-Go: **GO**
- Phase 2: **closed**

## 7. Phase-3 Working Rule

- use the existing canonical operator flow unchanged
- treat API, dashboard, telegram, and CLI as one truth chain
- prioritize clarity and consistency before new capabilities

## 8. S50A Path Classification

- canonical: the only runtime source of truth
- alias: compatibility pointer to canonical
- superseded: replaced path (not for new use)
- provisional: not yet accepted into locked inventory
- inventory reference: `CANONICAL_SURFACE_INVENTORY.md`

## 9. S50A Freeze Gate (Current)

- inventory is delivered and freeze-accepted
- Claude governance review: PASS
- Antigravity readability/onboarding review: PASS
- S50A is formally closed; S50B is now active
