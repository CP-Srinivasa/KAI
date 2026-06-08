# KAI (Repo-/Paketname: `ai_analyst_trading_bot`)

**KAI ist ein modulares, sicheres und agentisches KI-System für globale Informations-, Markt-, Risiko- und Finanzanalyse** — kein einfacher Trading-Bot und keine Blackbox. KAI trennt Datenaufnahme, Analyse, Risiko, Entscheidung, Audit, Sicherheit, Benutzerinteraktion und optionale Ausführung klar voneinander, mit Watchdog-Kontrolle und SENTR-Sicherheit. Die vollständige Identitäts- und Zielbild-Definition (inkl. Schichtenmodell und Reifegrade) ist die Single Source of Truth in **[`docs/KAI_IDENTITY.md`](docs/KAI_IDENTITY.md)**.

`ai_analyst_trading_bot` ist der Legacy-/Repository-/Paketname; `Robotron` ist ein interner Codename — beide sind nicht die fachliche Produktidentität.

**Heute live (Paper-First, Live-Execution disabled):** crypto/market intelligence pipeline —
RSS + TradingView + Telegram ingestion → LLM/rule analysis → scoring → alerting → paper-trading signal bridge. Dashboard + Cloudflare Tunnel for remote operator access. Zukunftsschichten (Lightning, DeFi, KYT, öffentliche Tor-Analyse, App/Multichannel, Payment-/Spenden-/Investment-Flows) sind im Zielbild beschrieben und gegated — siehe `docs/KAI_IDENTITY.md`.

## Current State (2026-06-08)

| Field | Value |
|---|---|
| Phase | Re-entry + Stabilisierung (post-PHASE-5-suspension) |
| Status | `ACTIVE` — Re-Entry vollzogen; `RE_ENTRY_MODE` live |
| Source of truth | Pi 5 (`ubuntu@192.168.178.23`), live seit 2026-05-07 |
| Active workstream | Asset-Reserve/Fokusfeld-Layer (D-228/S3), Dispatch-Recall-Proxy (D-227), Diversification enforce (D-226) |
| Live execution | OFF — paper/approval-mode only; Live-Gates ungeöffnet |

See `DECISION_LOG.md` for full decision history. Latest entries: **D-228/S3** (Asset-Reserve + Fokusfeld-Taxonomie + Enforce-Cap), **D-227** (Dispatch-Recall-Proxy + tunable bullish gate), **D-226** (Asset-Diversification enforce). Der frühere `SUSPENDED`-Zustand (D-125, TradingView-Pivot) is seit dem Re-Entry am 2026-05-07 abgelöst.

## Stack at a Glance

| Component | Status |
|---|---|
| FastAPI server (`app/api/main.py`) | in-process RSS scheduler + position monitor |
| Telegram operator bot | polling, admin-chat approval flow |
| Cloudflare Named Tunnel | `kai-trader.org` (live, auto-started by `scripts/server_start.sh`) |
| Paper-trading cron (Windows Task Scheduler) | every 10 min — BTC/USDT + ETH/USDT paper cycles, monitor, bridge, freshness check, liveness watchdog |
| Agent worker | SENTR · Watchdog · Architect · DALI · Neo · SATOSHI (Claude Code only) |
| Dashboard SPA | React under `/dashboard/` · mobile-friendly |

## Quick Start

```bash
bash scripts/server_start.sh              # full stack (API + tunnel + agent-worker + cron status)
bash scripts/server_status.sh             # health + sources + log tail
bash scripts/server_stop.sh               # clean stop
bash scripts/server_restart.sh            # stop + start
```

Opt-outs: `KAI_TUNNEL=0` · `KAI_AGENT_WORKER=0` · `KAI_CRON=0` · `KAI_BIND_LAN=1`

Local access: `http://127.0.0.1:8000/dashboard/`
Remote access: `https://kai-trader.org/dashboard/` (if WARP paused on client — see memory `reference_cloudflare_warp_conflict.md`)

## Daily Operator Commands

```bash
# Health + diagnostics
python -m app.cli.main /status                             # operator summary (positions, backlog, alert-rate, cycles)
python -m app.cli.main alerts pending-annotations          # directional alerts awaiting outcome
python -m app.cli.main alerts tv4-quality-bar              # per-source precision with Wilson 95% CI

# Pipeline manual trigger (cron does this automatically every 10/30/40 min)
python -m app.cli.main pipeline run-all --top-n 1          # all active RSS feeds in one pass
python -m app.cli.main pipeline newsdata "..." --size 10   # NewsData.io batch
python -m app.cli.main pipeline twitter --top-n 5          # X/Twitter social feed

# Paper trading (cron default: every 10 min)
python -m app.cli.main trading run-once --symbol BTC/USDT --mode paper --provider coingecko
python -m app.cli.main trading monitor-positions --provider coingecko
python -m app.cli.main trading operator-signal-bridge-tick

# Alerts + annotation
python -m app.cli.main alerts auto-annotate                # resolves directional alerts via price check
python -m app.cli.main alerts annotate <document_id> <hit|miss|inconclusive>
python -m app.cli.main alerts hold-report                  # forward-precision + hold-gate metrics
python -m app.cli.main alerts backfill-provenance --dry-run

# Daily strategy (cron runs bootstrap; operator reviews + fills)
python -m app.cli.main daily-strategy bootstrap            # idempotent skeleton for today
```

## Safety Minimum (non-negotiable)

- **No live execution path.** Paper + approval-mode only. Operator approves each filled signal.
- **Fail-closed by default.** Stale market data → cycle skipped (not silently executed).
- **No secrets in repo** (`.gitignore` protects `.env*`, DB files, artifacts).
- **Approval-mode pflicht** for operator-signal bridge (`EXECUTION_OPERATOR_SIGNAL_APPROVAL_ENABLED=true`).
- **Trust-boundary `monitor/*`**: operator-curated files govern trusted-author bypass, keyword extraction, source whitelists — file-system ACL is the trust line. See AGENTS.md § Operator-Trust-Boundary.

## TradingView Pivot (D-125)

TV-1..TV-4 stages audit-only, fail-closed, gated by shared-token + HMAC. TV-4b bridge writes TV events to `alert_audit.jsonl` for Auto-Annotator resolve. See memory `project_tv_pivot.md` for the 5 non-negotiable conditions. Scheduler is opt-in via `TRADINGVIEW_BRIDGE_SCHEDULER_ENABLED=true`.

## Canonical Living Docs

- `docs/KAI_IDENTITY.md` — **Single Source of Truth** für Projektidentität + Zielbild-Schichtenmodell
- `AGENTS.md` — operator constraints, current phase state, agent roster
- `RUNBOOK.md` — daily operator procedure, dashboard, agent chat
- `DECISION_LOG.md` — compact decision history (D-1..D-188)
- `CLAUDE.md` — execution directive for all coding agents
- `docs/contracts.md` — core contracts and invariants

Historical governance artifacts archived in `docs/archive/`.

## Development

```bash
pip install -e .                                           # editable install
python -m pytest                                           # ~4585 tests baseline
python -m ruff check .
cd web && npm install && npm run build                     # dashboard SPA
```

See `CLAUDE.md` for architecture rules and agent collaboration contracts.
