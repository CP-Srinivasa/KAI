# KAI (Repo-/Paketname: `ai_analyst_trading_bot`)

**KAI ist ein modulares, sicheres und agentisches KI-System für globale Informations-, Markt-, Risiko- und Finanzanalyse** — kein einfacher Trading-Bot und keine Blackbox. KAI trennt Datenaufnahme, Analyse, Risiko, Entscheidung, Audit, Sicherheit, Benutzerinteraktion und optionale Ausführung klar voneinander, mit Watchdog-Kontrolle und SENTR-Sicherheit. Die vollständige Identitäts- und Zielbild-Definition (inkl. Schichtenmodell und Reifegrade) ist die Single Source of Truth in **[`docs/KAI_IDENTITY.md`](docs/KAI_IDENTITY.md)**.

`ai_analyst_trading_bot` ist der Legacy-/Repository-/Paketname; `Robotron` ist ein interner Codename — beide sind nicht die fachliche Produktidentität.

## Ziel-Hierarchie (bindend, Operator-Klarstellung 2026-07-02)

Die **Gesamt-Vision ist das Dach und unverändert gültig**: eine institutionelle AI-Finanzanalyse- und Entscheidungs-Infrastruktur — Qualität, probabilistische Entscheidungsfindung, Datenvalidierung, Risikoarchitektur, langfristige Lernfähigkeit, Multi-Agenten-System. Kein gewöhnlicher Trading-Bot. Kanonische Definition: [`docs/KAI_IDENTITY.md`](docs/KAI_IDENTITY.md).

**Current waypoint darunter: [ADR 0012](docs/adr/0012-north-star-pivot-research-truth-platform.md) — Research-/Truth-Plattform für auditierbare Markt-Signal-Falsifikation.** Die Falsifikations-Disziplin IST die Qualitäts- und Validierungs-Schicht der Vision, kein Ersatz für sie (siehe ADR-0012-Addendum 2026-07-02). Zugangs-/Realisierungs-Achse: [ADR 0013](docs/adr/0013-frontier-and-boundary.md). Endziel bleibt ein unverzichtbarer, nachgefragter Use Case, in dem KAI unschlagbar ist.

**Heute real im Betrieb (Paper-First, Live-Execution disabled):**

- **Wahrheitskette live (Pi):** Prä-Registrierungs-Ledger → Hypothesen-Eval → prereg-check → attestiertes Verdikt → Family-Status/Stop-Rule, tamper-evident verankert (Hash-Chain + OpenTimestamps).
- **Paper-Trading-Maschine läuft bewusst weiter — als Messinstrument des Labors** (kosten-ehrliche Fill-/Slippage-/Fee-Wahrheit für Falsifikations-Urteile), nicht als Alpha-Produkt. Alle zugänglichen Signal-Familien sind statistisch widerlegt (canonical-edge n=68, P(mu_net>0)=10,44 %; Momentum n=178) — siehe ADR 0007/0012.
- **Ingestion/Analyse-Pipeline:** RSS + TradingView + Telegram → LLM-/Regel-Analyse → Scoring → Alerting → Paper-Bridge; Dashboard + Cloudflare Tunnel für Operator-Remote-Zugang.
- **LN-/Blockchain-Schicht:** kapitalfreier Kern live (L1 Fee-Truth, L3-OTS); Wert-Schicht (Zahlungen) policy-gegated und inert.

Zukunftsschichten (DeFi, KYT, öffentliche Tor-Analyse, App/Multichannel, Payment-/Spenden-/Investment-Flows) bleiben im Zielbild beschrieben und gegated — siehe `docs/KAI_IDENTITY.md`.

## Current State (2026-07-02)

| Field | Value |
|---|---|
| Phase | Truth-Platform-Wegpunkt (ADR 0012) — Truth-Infra härten, Falsifikations-Qualität |
| Status | `ACTIVE` — Paper-/Lern-Phase; Live-Gates ungeöffnet |
| Source of truth | Pi 5 (`ubuntu@192.168.178.23`), live seit 2026-05-07 |
| Edge-Stand | alle zugänglichen Signal-Familien widerlegt; Zitat NUR via `trading canonical-edge` |
| Nachfrage | UNBEWIESEN, nicht widerlegt — G0-`/oracle`-Pfad war bis nach dem Pivot gated/ungelistet (ADR-0012-Addendum) |
| Live execution | OFF — paper/approval-mode only; Triple-Flag + ACK-Sentinel ungeöffnet |

See `DECISION_LOG.md` for decision history; ADRs unter `docs/adr/` (Index: [`docs/adr/README.md`](docs/adr/README.md)). Aktive Risiken: [`RISK_REGISTER.md`](RISK_REGISTER.md) · aktive Annahmen: [`ASSUMPTIONS.md`](ASSUMPTIONS.md) · Sicherheits-Überblick: [`SECURITY.md`](SECURITY.md).

## Stack at a Glance

| Component | Status |
|---|---|
| FastAPI server (`app/api/main.py`) | in-process RSS scheduler + position monitor |
| Telegram operator bot | polling, admin-chat approval flow |
| Cloudflare Named Tunnel | `kai-trader.org` (live, auto-started by `scripts/server_start.sh`) |
| Paper-trading scheduling (systemd-Timer auf Pi 5) | paper cycles, position monitor, bridge, entry-watch, freshness check, liveness watchdog |
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
python -m app.cli.main alerts ops-status                   # operator summary (positions, backlog, alert-rate, cycles)
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
- `docs/adr/README.md` — ADR-Index (0001–0013) inkl. Nummern-Hygiene
- `SECURITY.md` — kanonischer Sicherheits-Überblick (Threat-Model, Audit-/Attestation-Kette, Spec-Index)
- `RISK_REGISTER.md` — aktive Risiken (lebend)
- `ASSUMPTIONS.md` — aktive Annahmen mit Status (lebend)
- `AGENTS.md` — operator constraints, current phase state, agent roster
- `RUNBOOK.md` — daily operator procedure, dashboard, agent chat
- `DECISION_LOG.md` — compact decision history
- `CLAUDE.md` — execution directive for all coding agents
- `docs/contracts.md` — core contracts and invariants

Historical governance artifacts archived in `docs/archive/`.

## Development

```bash
pip install -e .                                           # editable install
python -m pytest                                           # ~1946 tests baseline
python -m ruff check .
cd web && npm install && npm run build                     # dashboard SPA
```

See `CLAUDE.md` for architecture rules and agent collaboration contracts.
