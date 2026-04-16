# RUNBOOK.md

## Scope

Canonical operator runbook for the active PH5 hold period.
Goal: run the pipeline daily, collect directional alerts, and annotate outcomes until the gate is met.

Gate: no new feature work until at least 50 directional alerts are resolved (`hit` or `miss`).

## 1. Baseline Check

```bash
python -m pytest
python -m ruff check .
```

## 2. Daily Core Routine

```powershell
powershell -ExecutionPolicy Bypass -File scripts/ph5_daily_ops.ps1
python scripts/ph5_hold_metrics_report.py
```

The routine does:
- `pipeline-run`
- `alerts auto-check` (historical horizon check, default dry-run)
- `alerts hold-report`
- `alerts pending-annotations`

## 3. Manual Operator Commands

```bash
python -m app.cli.main pipeline-run <feed_url> --source-id <id> --source-name <name> --top-n 5
python -m app.cli.main analyze pending --limit 50
python -m app.cli.main signals extract --limit 20 --min-priority 8
python -m app.cli.main alerts evaluate-pending
python -m app.cli.main alerts auto-check --threshold-pct 5 --horizon-hours 24 --min-age-hours 24 --dry-run
python -m app.cli.main alerts hold-report
python -m app.cli.main alerts baseline-report --input-path artifacts/ph4b_tier3_shadow.jsonl
python -m app.cli.main alerts pending-annotations --limit 20 --min-age-hours 24
python -m app.cli.main alerts annotate <document_id> <hit|miss|inconclusive>
python scripts/ph5_keyword_coverage_audit.py --limit 300 --target-coverage 80 --suggestions 30
```

## 4. Hold Gate Review

Use the generated report under `artifacts/ph5_hold/`.

Primary checks:
- resolved directional alerts (`hit` + `miss`) >= 50
- alert precision from resolved outcomes
- paper-trading evidence present

If the gate is not met, continue daily operation and annotation only.

## 5. Guardrails

- Keep execution in paper/shadow-safe mode (no live execution)
- Do not add new sprint-contract documents
- Do not add new companion-ML feature work while hold is active
- Record decisions compactly in `DECISION_LOG.md`

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

## 7. Agenten (SENTR · Watchdog · Architect)

Drei Claude-Code-only-Agenten mit ehrlicher Status-Erkennung über JSONL-Dropbox
unter `artifacts/agents/{sentr,watchdog,architect}/`. Keine Fake-Heartbeats.

| Agent | ID | Modi |
|---|---|---|
| SENTR | `a708ac129e9cf2569` | inspect, report |
| Watchdog | — | check, report |
| Architect | `a14a2b53ba50ebadd` | review, propose |

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
