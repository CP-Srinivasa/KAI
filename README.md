# KAI (Robotron)

KAI is a modular, security-first, audit-first AI analysis and operator platform.
Default runtime remains `paper`/`shadow` with fail-closed controls.

## Current Phase

- phase: `PHASE 5 (active) — Signal Reliability & Trust` | sprint: `PH5C_FILTER_BEFORE_LLM_BASELINE (results review)`
- technical baseline: `1449 passed, ruff clean, mypy 0 errors` | runtime: paper/shadow, fail-closed

## Phase-5 Focus

Phase 4 (signal quality calibration, 11 sprints PH4A-PH4K) is formally closed (D-87, 2026-03-24).
Phase 5 investigates signal reliability and trust from the PH5A reliability baseline:

- PH5A closed (D-89): reliability baseline — LLM-error-proxy 27.5% (19/69), priority-mean 3.96, tag-fill 100%
- PH5B closed (D-94, §84): root cause confirmed — 19/19 low-signal docs are `EMPTY_MANUAL` (no ingested content)
- PH5C execution complete (D-96, §85): stub-document pre-filter baseline established; pending results review and close
- Alert Integration active: `analyze-pending` now dispatches alerts (Phase 4 of CLI pipeline) with `--no-alerts` flag
- CI hardened: all 5 jobs green, `hypothesis` + `pytest-mock` in dev-deps, bandit B324 fixed
- CoinGecko active as default market data provider (free tier, ~1min delayed, no API key required)
- Paper-trading loop active: `run-once` command available, fail-closed on live
- Freshness enforcement active: stale market data → cycle skipped with explicit STALE_DATA audit entry

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
python -m pytest            # 1449+ tests
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
| `APP_MARKET_DATA_PROVIDER` | `coingecko` | Market data source: `coingecko` (real, free-tier) or `mock` (dev/test only — logs WARNING) |
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

Dashboard: `GET /dashboard` — read-only operator summary (HTML, no auth required).

## Active vs. Experimental Features

### Active (production default)

| Feature | Status | Notes |
|---|---|---|
| Rule-based analysis pipeline | ✅ active | keyword scoring, signal generation |
| CoinGecko market data | ✅ active | free tier, ~1min delayed, 10 symbols |
| Paper trading loop | ✅ active | fail-closed, paper/shadow only |
| Operator API (`/operator/*`) | ✅ active | Bearer auth required |
| Telegram operator bot | ✅ active | HMAC-verified webhooks |
| CLI `research` surface | ✅ active | daily-summary, readiness, positions etc. |
| Alerting (Telegram/Email) | ✅ active | threshold-based, dry-run default |

### Experimental (not in default operator workflow)

| Feature | Status | Activation |
|---|---|---|
| Companion ML pipeline | 🔬 experimental | No model deployed. Requires `COMPANION_MODEL_ENDPOINT`. CLI: `benchmark-companion`, `check-promotion`, `record-promotion` |
| Multi-path inference (A/B/C) | 🔬 experimental | Requires companion model + `route-activate` MCP tool |
| ABCInferenceEnvelope | 🔬 experimental | Only active in `primary_with_shadow` / `control` route modes |
| Shadow inference | 🔬 experimental | Needs active route profile with shadow paths |
| Upgrade cycle / promotion | 🔬 experimental | Part of companion ML pipeline |

### Not introduced (by design)

- Event sourcing
- Multi-tenant support
- Kafka / message queue infrastructure

These are not planned for the current phase.

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
