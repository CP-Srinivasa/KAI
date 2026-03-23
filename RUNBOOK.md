# RUNBOOK.md

## Scope

Canonical operator runbook for Phase-3 / S50.
Phase 2 is formally closed (2026-03-22). Phase 3 is active.
Current active sprint: `S50_CANONICAL_CONSOLIDATION_BASELINE`.
Next required step: `S50A_CANONICAL_PATH_INVENTORY`.
Scope in S50 is consolidation-only (no new feature rollout).

## 1. Baseline Quality Check

```bash
python -m pytest
python -m ruff check .
```

Current validated baseline: `1519 passed`, `ruff clean`.

## 2. Daily Operator Entry (CLI)

```bash
research daily-summary
research readiness-summary
research decision-pack-summary
research trading-loop-status
research trading-loop-recent-cycles
research paper-positions-summary
research paper-exposure-summary
```

## 3. Drilldown and History (CLI)

```bash
research review-journal-summary
research resolution-summary
research alert-audit-summary
```

## 4. Dashboard

```text
http://localhost:8000/dashboard
```

- canonical path: `GET /dashboard`
- no `/static/dashboard.html`
- no second aggregate path

## 5. Operator API Chain

```text
GET /operator/daily-summary
GET /dashboard
GET /operator/readiness
GET /operator/decision-pack
GET /operator/trading-loop/status
GET /operator/trading-loop/recent-cycles
GET /operator/portfolio-snapshot
GET /operator/exposure-summary
GET /operator/review-journal
GET /operator/resolution-summary
GET /operator/alert-audit
```

## 6. Guardrails

- auth required: `Authorization: Bearer <APP_API_KEY>`
- guarded endpoint requires: `Idempotency-Key`
- `mode=live` is fail-closed
- no unverified model output on critical actions
- no second aggregation backbone

## 7. Final Acceptance (2026-03-22)

| Kanal | Status |
|---|---|
| API 12/12 | PASS |
| Dashboard | PASS |
| Telegram 11/11 | PASS |
| CLI daily baseline (7) | PASS |
| CLI `review-journal-summary` | PASS |
| CLI `resolution-summary` | PASS |
| CLI `alert-audit-summary` | PASS |

## 8. Phase-2 Final Acceptance Record

Executed and validated:

```bash
research review-journal-summary
research resolution-summary
research alert-audit-summary
```

No unhandled exception, `execution_enabled=False` present, no technical defect.
Go/No-Go: **GO**.
Phase 2: **formally closed**.

## 9. Phase-3 S50 Working Mode

- keep the operator flow unchanged and stable
- resolve documentation and naming drift only
- no new trading, broker, or live execution features

## 10. S50A Inventory Checklist

- confirm canonical runtime paths for API, dashboard, telegram, CLI, and MCP
- confirm alias mappings and superseded names
- mark provisional paths explicitly
- do not start refactoring before inventory freeze
- canonical inventory artifact: `CANONICAL_SURFACE_INVENTORY.md`
