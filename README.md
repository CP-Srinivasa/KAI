# KAI (Robotron)

KAI is a modular, security-first, audit-first AI analysis and operator platform.
Default runtime remains `paper`/`shadow` with fail-closed controls.

## Current Phase

- phase: `PHASE 3 (active)`
- current sprint: `S50_CANONICAL_CONSOLIDATION_BASELINE` (active)
- next required step: `S50A_CANONICAL_PATH_INVENTORY`
- technical reference: `1519 passed, ruff clean`

## Phase-3 Kickoff Focus

Phase 2 is formally complete and accepted.
Sprint 50 opens Phase 3 with consolidation-only scope:

- canonical architecture and naming clarity
- synchronized governance and operator docs
- no new feature depth before consolidation acceptance
- no second aggregation backbone and no new execution semantics

Operator surfaces remain the accepted baseline across API, Dashboard, Telegram, and CLI.

## S50A Focus

S50A is inventory-first:

- identify canonical runtime paths
- classify aliases and superseded paths
- keep provisional paths explicit for later review
- avoid refactoring before inventory freeze

## Core Principles

- simple but powerful
- security first
- fail closed, not fail open
- no hidden side effects
- no unverified critical execution
- live default-off

## Quick Start

```bash
pip install -e ".[dev]"
python -m pytest
python -m ruff check .
uvicorn app.api.main:app --reload
```

## Daily Operator Flow (Canonical)

```bash
trading-bot research daily-summary
trading-bot research readiness-summary
trading-bot research decision-pack-summary
trading-bot research paper-positions-summary
trading-bot research paper-exposure-summary
trading-bot research trading-loop-status
trading-bot research trading-loop-recent-cycles
trading-bot research review-journal-summary
trading-bot research resolution-summary
trading-bot research alert-audit-summary
```

## Optional Guarded Single Cycle (Paper/Shadow)

```bash
trading-bot research trading-loop-run-once --mode paper --symbol BTC/USDT
```

## Operator API Drilldown/History Read Endpoints

- `GET /operator/review-journal`
- `GET /operator/resolution-summary`
- `GET /operator/alert-audit`

These endpoints are read-only delegation surfaces and use the same fail-closed
auth/error governance as all `/operator/*` routes.

## Documentation Index

- [PHASE_PLAN.md](PHASE_PLAN.md)
- [SPRINT_LEDGER.md](SPRINT_LEDGER.md)
- [DECISION_LOG.md](DECISION_LOG.md)
- [RISK_REGISTER.md](RISK_REGISTER.md)
- [SECURITY.md](SECURITY.md)
- [RUNBOOK.md](RUNBOOK.md)
- [ONBOARDING.md](ONBOARDING.md)
- [ARCHITECTURE.md](ARCHITECTURE.md)
- [CANONICAL_SURFACE_INVENTORY.md](CANONICAL_SURFACE_INVENTORY.md)
- [ASSUMPTIONS.md](ASSUMPTIONS.md)
- [CHANGELOG.md](CHANGELOG.md)
