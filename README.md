# KAI (Robotron)

KAI is a modular, security-first, audit-first AI analysis and operator platform.
Default runtime remains `paper`/`shadow` with fail-closed controls.

## Current Phase

- phase: `PHASE 4 (active)` | sprint: `PH4F closed → PH4G in selection`
- technical baseline: `1519 passed, ruff clean` | runtime: paper/shadow, fail-closed

## Phase-4 Focus

Phase 3 (canonical consolidation, S50) is formally complete.
Phase 4 runs signal quality audits on the frozen PH4A–PH4D evidence arc:

- PH4A–PH4D arc closed: keyword expansion improved good-hit `13→18`, zero-hit `29→26`, no regressions
- PH4E closed (D-67): scoring divergence root cause = defaults-by-design (RuleAnalyzer leaves LLM fields to LLM)
- PH4F closed (D-70): rule-input completeness diagnostic — field-level gap analysis complete, no runtime changes
- PH4G: next sprint in selection — fallback input enrichment baseline
- CoinGecko integration active as default market data provider (free tier, ~1min delayed)
- Paper-trading loop active: `run-once` command available, fail-closed on live

## Core Principles

- simple but powerful
- security first
- fail closed, not fail open
- no hidden side effects
- no unverified critical execution
- live default-off

## Prerequisites

- Python 3.12+
- PostgreSQL (for DB-backed features; tests run without it)
- `.env` file based on `.env.example`

## Quick Start

```bash
pip install -e ".[dev]"
cp .env.example .env        # edit as needed
python -m pytest            # 1519 tests
python -m ruff check .
uvicorn app.api.main:app --reload
```

## Environment Variables (Key)

| Variable | Default | Description |
|---|---|---|
| `APP_ENV` | `development` | Set to `production` to disable Swagger/ReDoc and tighten defaults |
| `APP_API_KEY` | `` | Bearer token for API auth. Leave empty for local dev only. |
| `APP_CORS_ALLOWED_ORIGINS` | `http://localhost:3000,http://localhost:8000` | Comma-separated allowed CORS origins. Set explicitly for production. |
| `DB_URL` | `postgresql+asyncpg://...` | Database connection string |
| `OPENAI_API_KEY` | — | Required for LLM analysis |
| `OPERATOR_TELEGRAM_BOT_TOKEN` | — | Telegram operator bot token |
| `OPERATOR_ADMIN_CHAT_IDS` | — | Comma-separated admin Telegram chat IDs |

Full variable reference: `.env.example`

## Production Notes

Setting `APP_ENV=production` activates:
- Swagger UI (`/docs`), ReDoc (`/redoc`), and OpenAPI schema (`/openapi.json`) are **disabled**
- CORS origins should be set via `APP_CORS_ALLOWED_ORIGINS` (no wildcard by default)

`APP_API_KEY` must be set to a strong random token in production:
```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
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

## Operator API

All `/operator/*` routes require Bearer auth (`APP_API_KEY`).

Key read endpoints:
- `GET /operator/status` — system status
- `GET /operator/health` — health check
- `GET /operator/positions` — paper positions
- `GET /operator/exposure` — exposure summary
- `GET /operator/trading-loop/status` — trading loop state
- `GET /operator/trading-loop/recent-cycles` — recent cycle history
- `POST /operator/trading-loop/run-once` — guarded paper/shadow cycle (fail-closed on live)

Dashboard: `GET /dashboard/` — read-only operator summary.

## Documentation Index

- [PHASE_PLAN.md](PHASE_PLAN.md)
- [SPRINT_LEDGER.md](SPRINT_LEDGER.md)
- [DECISION_LOG.md](DECISION_LOG.md)
- [RISK_REGISTER.md](RISK_REGISTER.md) — aktive technische Schulden und Risiken (V-1..V-9)
- [SECURITY.md](SECURITY.md)
- [RUNBOOK.md](RUNBOOK.md)
- [ONBOARDING.md](ONBOARDING.md)
- [ARCHITECTURE.md](ARCHITECTURE.md)
- [CANONICAL_SURFACE_INVENTORY.md](CANONICAL_SURFACE_INVENTORY.md)
- [KAI_AUDIT_TRAIL.md](KAI_AUDIT_TRAIL.md) — Audit-Verlauf und Befund-Abschluss
- [ASSUMPTIONS.md](ASSUMPTIONS.md)
- [CHANGELOG.md](CHANGELOG.md)
