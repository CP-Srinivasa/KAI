# RUNBOOK.md

## Scope

Canonical operator runbook for the **Re-entry + Stabilisierung** period (post-PHASE-5-suspension; the TradingView-Pivot suspension D-125 was lifted at the 2026-05-07 Pi-5 cutover).
Primary goals: (a) keep pipeline + TV ingestion + paper-bridge alive 24/7 on the Pi 5 (`ubuntu@192.168.178.23`, source of truth), (b) approve operator signals via Telegram, (c) operate the diversification/asset-reserve layer (D-226/D-228) in paper, (d) keep live execution OFF until the live gates are explicitly opened.

## 1. Baseline Check

```bash
python -m pytest                                           # ~1946 tests
python -m ruff check .
```

## 2. Daily Core Routine (mostly automatic)

The Windows scheduled task `KAI-PaperTrading` runs every 10 min and performs:
- `Ensure-Server` liveness watchdog (full-stack restart via `server_start.sh` on down-detect; incidents logged to `artifacts/watchdog_incidents.jsonl`)
- `trading monitor-positions` (SL/TP triggers)
- `trading operator-signal-bridge-tick` (approved signals → paper fills)
- `trading run-once` BTC/USDT + ETH/USDT (paper mode, CoinGecko)
- Every 6th tick (~hourly): `alerts auto-annotate`
- Every 4th tick (~40 min): `pipeline run-all`
- Every 3rd tick (~30 min): `pipeline newsdata`
- Every 12th tick (~2h): `pipeline youtube`
- Every 6th tick (~hourly): `pipeline twitter`
- Each tick: `tradingview run` (TV-4 bridge), `freshness_check.py`
- First run after 08:00: `alerts daily-briefing`, `alerts health-check`, `daily-strategy bootstrap`

Manual re-trigger:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\paper_trading_cron.ps1
```

Install / remove the scheduled task:

```powershell
scripts\paper_trading_cron.ps1 -Install
schtasks /Delete /TN "KAI-PaperTrading" /F
```

Bash port (used on Pi after migration):

```bash
bash scripts/paper_trading_cron.sh
```

## 3. Manual Operator Commands

```bash
# Health + diagnostics
python -m app.cli.main /status
python -m app.cli.main alerts pending-annotations --limit 20 --min-age-hours 0
python -m app.cli.main alerts tv4-quality-bar --output-path artifacts/ph5_hold/quality_bar_<YYYYMMDD>.json
python -m app.cli.main alerts hold-report

# Pipeline
python -m app.cli.main pipeline run-all --top-n 1
python -m app.cli.main pipeline newsdata "crypto bitcoin ethereum solana" --language en --category business --size 10 --top-n 3
python -m app.cli.main pipeline twitter --top-n 5
python -m app.cli.main pipeline youtube <channel_url> --max-results 3 --top-n 1

# Paper trading (manual)
python -m app.cli.main trading run-once --symbol BTC/USDT --mode paper --provider coingecko --analysis-profile conservative
python -m app.cli.main trading monitor-positions --provider coingecko
python -m app.cli.main trading operator-signal-bridge-tick

# Alerts + annotation
python -m app.cli.main alerts auto-annotate
python -m app.cli.main alerts annotate <document_id> <hit|miss|inconclusive>
python -m app.cli.main alerts backfill-provenance --dry-run

# TradingView
python -m app.cli.main tradingview run
python -m app.cli.main alerts tv-bridge                    # TV events → alert_audit.jsonl

# Daily strategy
python -m app.cli.main daily-strategy bootstrap            # writes artifacts/daily_strategy/<today>.md
```

Allowed annotation outcomes: `hit`, `miss`, `inconclusive`.

## 4. Quality-Bar Review (replaces old Hold Gate review)

Artefacts under `artifacts/ph5_hold/`:
- `quality_bar_<YYYYMMDD>.json` — per-source precision + Wilson 95% CI
- `ph5_hold_metrics_report.json` — forward-simulation (actionable + priority gates)

Primary checks toward Re-Entry (2026-05-16):
- `resolved_directional_documents ≥ 200` (D-125 condition)
- `order_filled_count ≥ 3` (D-125 floor; target ≥ 10 with PnL)
- Active-precision (ex `unknown`-source) trending above gate
- Per-source CIs disjoint enough to distinguish signal quality

Decision record: always in `DECISION_LOG.md` (compact, 1–N lines). No sprint-contract docs.

## 5. Guardrails

- Paper/approval-mode only. No live execution path. (`EXECUTION_PAPER_MIN_PRIORITY` gate flag-gated, default 1)
- Fail-closed on stale market data — cycle skipped, logged with `market_data_source`.
- Approval-mode pflicht for operator-signal bridge (`EXECUTION_OPERATOR_SIGNAL_APPROVAL_ENABLED=true`, `_TTL_MINUTES=60`).
- No sprint-contract docs (D-99). Decisions in `DECISION_LOG.md` or code comments.
- `monitor/*` is operator-trust-boundary (D-181) — file-system ACL is the trust line.
- Provenance persistence pflicht (D-125/SAT-C-PROV-20260422-001): every outcome/audit row carries `source+version+signal_path_id+auth_method+ingest_event_id+provenance_hash`.
- Zero-downtime key rotation for `ALERT_PROVENANCE_SECRET` via 4-phase runbook in `.env.example` (D-183).

## 6. Operator Dashboard (D-140)

Single UI: React-SPA served by FastAPI under `/dashboard`.

```bash
bash scripts/server_start.sh                    # starts API on 127.0.0.1:8000
# open:  http://127.0.0.1:8000/dashboard/
```

Dashboard requires a bearer token for operator API calls. Set it once via the
banner at the top of the SPA (value is `APP_API_KEY` from `.env`, stored in
browser `localStorage` under `kai-api-key`).

### Rebuilding the SPA after frontend changes

```bash
cd web
npm install                 # first time only
npm run build               # produces web/dist/ — served at /dashboard/
```

Without `web/dist/` the FastAPI startup logs `dashboard_spa_build_missing` and
only the JSON endpoint `/dashboard/api/quality` is reachable.

### Frontend dev-server with HMR (optional)

```bash
cd web
npm run dev                 # http://127.0.0.1:5173/  (proxies API to :8000)
```

### LAN-reachable mode (opt-in)

```bash
KAI_BIND_LAN=1 bash scripts/server_start.sh
```

The server binds `0.0.0.0:8000`. Windows firewall: allow inbound TCP/8000 for
the private profile once. Do not enable on untrusted networks — the API key
is the only auth boundary.

## 7. Agenten (6 Claude-Code-only Specialists)

JSONL-Dropbox unter `artifacts/agents/{sentr,watchdog,architect,dali,neo,satoshi}/`. Keine Fake-Heartbeats. Volle Roster-Definition + Auto-Routing-Map: `AGENTS.md § Agent Roster` + `CLAUDE.md § Auto-Routing-Pflicht`.

| Agent | ID | Modi | Rolle |
|---|---|---|---|
| SENTR | `a708ac129e9cf2569` | inspect, report | Security/Inspection — Code, Configs, Secrets, Auditierbarkeit |
| Watchdog | — | check, report | Health/Drift — Pipeline-Outputs, Quality-Bar, Regressionen |
| Architect | `a14a2b53ba50ebadd` | review, propose | Architektur/Struktur — Module, Abhängigkeiten, Refactor |
| DALI | — | audit, propose, implement | Design/UI — Dashboard, Telegram, Visual System (Patch-Proposals, nie Auto-Apply) |
| Neo | — | analyze, propose, implement | Code-Tiefenanalyse — Root-Cause, Concurrency, Datenfluss, Performance |
| SATOSHI | — | crypto-review, forensic, threat-model, propose, implement | Krypto/Wallet/Smart-Contract/Forensik — Signaturen, HMAC, Webhooks, Provenance, Threat-Models |

UI: Dashboard → Sidebar → "Kontrolle" → "Agenten" (`#agents`).

```bash
# Inventar abfragen
curl -H "Authorization: Bearer $APP_API_KEY" http://127.0.0.1:8000/operator/agents

# Detail (inkl. recent_findings + recent_runs)
curl -H "Authorization: Bearer $APP_API_KEY" http://127.0.0.1:8000/operator/agents/watchdog

# Kommando in Queue legen (out-of-band ausgeführt)
curl -X POST -H "Authorization: Bearer $APP_API_KEY" -H "Content-Type: application/json" \
  -d '{"mode":"check","note":"manual op"}' \
  http://127.0.0.1:8000/operator/agents/watchdog/commands
```

**Dropbox-Konvention** — der Agent schreibt selbst nach:
- `findings.jsonl` — pro Zeile: `{"ts":"…","severity":"…","title":"…","detail":"…"}`
- `runs.jsonl` — pro Zeile: `{"ts":"…","mode":"check","result":"ok","duration_ms":1234}`
- `commands.jsonl` — wird vom HTTP-Endpoint/Telegram-Bot befüllt; Agent-Reader markiert verarbeitete Einträge
- `conversation.jsonl` — Single-Source-of-Truth für Dashboard + Telegram + Agent-Replies

Status-Logik: `live` = jüngster ts in findings/runs ≤ 24h; `prepared` = Verzeichnis
existiert, aber keine 24h-Aktivität; `unavailable` = Verzeichnis fehlt.

### Unified Chat (Dashboard ↔ Telegram ↔ Agent)

Alle drei Kanäle schreiben in **denselben** `conversation.jsonl`-Stream pro Agent.
Event-Shape: `{id, ts, agent, source: dashboard|telegram|agent, role: operator|agent, kind: message|command|finding|report, content, meta}`.

**Dashboard:** `#agents` → Tab "Chat" je Agent. 5s-Poll, Ctrl+Enter sendet.

**Telegram (Admin-Chat):**

```
/watchdog                    # letzte 5 Events aus conversation.jsonl
/watchdog <freitext>         # operator-Nachricht (source=telegram)
/watchdog !check <note>      # Kommando einreihen (wie Dashboard-Button)
/sentr  !inspect ...         # SENTR: inspect|report
/architect !review ...       # Architect: review|propose
/dali !audit ...             # DALI: audit|propose|implement (Patch-Proposal)
/neo !analyze ...            # Neo: analyze|propose|implement
/satoshi !crypto-review ...  # SATOSHI: crypto-review|forensic|threat-model|propose|implement
```

**Agent-Reply-Konvention** — Claude Code schreibt Antworten via:

```python
from app.api.routers.agents import append_conversation_event
append_conversation_event(
    "watchdog", source="agent", role="agent",
    content="Check abgeschlossen — 0 kritische Findings.",
    kind="report",
    meta={"command_id": "...", "duration_ms": 1234},
)
```

Dashboard sieht die Antwort binnen ~5s (Poll). Telegram sieht sie beim nächsten
`/watchdog`-Aufruf. Automatischer Push-zu-Telegram (`role:agent`-Tailer an
Admin-Chat) ist derzeit nicht aktiv — Folge-Ticket.

### Agent-Worker (Auto-Reply)

`app/agents/worker.py` tailed alle drei `commands.jsonl`, führt gewählten Modus
aus, hängt das Ergebnis als `role:agent`-Event in `conversation.jsonl` an und
flippt die Queue-Zeile auf `status=done`. Freitext-Messages ohne Agent-Reply
bekommen einen einmaligen Auto-ACK pro Nachricht (mit Hinweis, dass Freitext
derzeit nicht inhaltlich beantwortet wird — `!mode` triggert Aktion).

```bash
bash scripts/agent_worker_start.sh      # daemon (Poll 5s, Log logs/agent_worker.log)
bash scripts/agent_worker_stop.sh
python -m app.agents.worker --once      # oneshot (z.B. in ph5_daily_ops.ps1)
```

Handler-Matrix: `watchdog.check|report`, `sentr.inspect|report`, `architect.review|propose`
— alle deterministisch, ohne LLM-Call. Substantielle Prüfungen (Hardcoded-Keys,
Artifact-Alter, Lint, Modulzahl) erzeugen echte Findings; `propose` ist bewusst
eine generische Leitplanken-Antwort.

**Raw API:**

```bash
# Conversation lesen (tail 20, oder nur neuer als ts)
curl -H "Authorization: Bearer $APP_API_KEY" \
  "http://127.0.0.1:8000/operator/agents/watchdog/messages?tail=20"

# Operator-Nachricht posten
curl -X POST -H "Authorization: Bearer $APP_API_KEY" -H "Content-Type: application/json" \
  -d '{"content":"bitte prüfen","source":"dashboard"}' \
  http://127.0.0.1:8000/operator/agents/watchdog/messages
```
