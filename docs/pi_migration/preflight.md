# Pi-Migration Pre-Flight Checklist

**Target date:** 2026-05-01 (D-11 ab 2026-04-20)
**Source:** Laptop (Windows 11, Git-Bash/MSYS, cwd `C:\Users\sasch\.local\bin\ai_analyst_trading_bot`)
**Target:** Raspberry Pi 4b (Linux, 24/7-Betrieb)
**Goal:** Vollständiger KAI-Stack läuft auf Pi; Laptop darf ohne Verlust offline gehen. Re-Entry-Gate 2026-05-16 (D-26) braucht dann eine ungestörte Datenakkumulation.

Diese Doku ist **Pre-Flight**, nicht Runbook: der User fährt am 2026-05-01 physisch zum Pi, vorher läuft hier nichts. Jeder Abschnitt endet mit einer expliziten **Verifikations-Frage** — erst wenn alle mit „ja" beantwortet sind, ist die Migration durchführbar.

---

## 1. Ist-Zustand (gemessen 2026-04-20)

| Komponente | Zustand Laptop |
|---|---|
| Server-Prozess | `uvicorn app.api.main:app` via `nohup`, PID in `.server.pid`, Log `logs/server.log` |
| Tunnel | `cloudflared tunnel run kai`, Named Tunnel UUID `beafc2ce-3c02-40c6-a6d7-359b2cc40cf6`, PID in `.tunnel.pid` |
| Agent-Worker | `nohup python` via `scripts/agent_worker_start.sh`, PID in `.agent_worker.pid` |
| In-Process-Scheduler | Telegram-Poller, RSSScheduler, PositionMonitor — **laufen im Server-Prozess**, kein extra Unit |
| Cron | Windows Task Scheduler `KAI-PaperTrading`, alle 10 min, `scripts/paper_trading_cron.ps1` |
| Daten | `artifacts/` = 43 MB (JSONL-Append-Only), `logs/` = 61 KB |
| Secrets | `.env` (63 Zeilen, 40+ Keys), siehe §4 für Kategorien |
| Cloudflared | `~/.cloudflared/` mit `cert.pem`, `beafc2ce-…json` (tunnel creds), `config.yml` (Ingress) |
| Domain | `kai-trader.org` → Cloudflare → Tunnel → `http://127.0.0.1:8000` |

**Verifikation:** Stimmen die Prozess-PIDs heute (`bash scripts/server_start.sh` zeigt Stack-Status)? → ja, am 2026-04-20 verifiziert.

---

## 2. Soll-Zustand (Pi)

Stack auf dem Pi läuft unter einem dedizierten Benutzer `kai` mit folgenden Units (systemd):

| Unit | Command | Restart | Why |
|---|---|---|---|
| `kai-server.service` | `uvicorn app.api.main:app --host 127.0.0.1 --port 8000` | `on-failure`, 10 s | Haupt-API, Dashboard, In-Process-Scheduler |
| `kai-agent-worker.service` | `python -m app.agents.worker` (Pfad via `scripts/agent_worker_start.sh` spiegeln) | `on-failure`, 15 s | Telegram-Auto-Replies, Agent-Queue |
| `cloudflared.service` | `cloudflared tunnel --config /home/kai/.cloudflared/config.yml run kai` | `always`, 10 s | Public Ingress `kai-trader.org` |
| `kai-paper-trading.timer` + `.service` | `python -m app.cli.main paper-trading cron` (oder Äquivalent des PS1) | timer, 10 min | Ersatz für Windows Task |
| `kai-daily-strategy.timer` + `.service` | `python -m app.cli.main daily-strategy bootstrap` | timer, täglich 08:00 Europe/Berlin | Ersatz für Cron-Hook in `paper_trading_cron.ps1` |

**Verifikation:** Ist auf dem Pi systemd verfügbar? Läuft Raspberry Pi OS (Bookworm oder neuer, Python 3.12+)? → offen, klärt User vor Ort.

---

## 3. Pre-Flight-Schritte (laufen VOR dem 2026-05-01)

### 3.1 Auf dem Laptop — heute bis 04-30
- [ ] **Git-Sync:** Repo ist in einem Git-Remote (Origin) vorhanden und aktuell. Falls nicht: anlegen (GitHub privat/SSH) und pushen. **Grund:** `artifacts/` ist .gitignored und 43 MB groß — wird separat migriert, aber Code muss über Git, nicht via USB.
- [ ] **Secret-Inventar:** Finale Version der `.env` dokumentiert (siehe §4) — jede Zeile bestätigt noch gültig? Rotierte Tokens (Telegram/CoinGecko/Anthropic, siehe Memory `security_e1_key_rotation.md`) sind bereits in der `.env` enthalten.
- [ ] **Cloudflared-Bundle zusammenstellen:** Die 3 Files `cert.pem`, `beafc2ce-3c02-40c6-a6d7-359b2cc40cf6.json`, `config.yml` aus `~/.cloudflared/` in eine verschlüsselte ZIP packen (z.B. `7z a -p pi_cloudflared.7z ...`). Diese 3 Files **müssen** auf den Pi — ohne sie kein Public-Ingress.
- [ ] **Artefakt-Tarball:** `tar -czf artifacts_2026-04-30.tar.gz artifacts/` — so wird der JSONL-Altbestand (354 resolved alerts, 43 fills, Audit-Trails) auf dem Pi unmittelbar weiterverarbeitet statt resettet.
- [ ] **Cron-Pattern extrahieren:** `paper_trading_cron.ps1` ist Windows-spezifisch. Äquivalente Logik für Linux-Timer ableiten (heute tun, nicht auf dem Pi improvisieren). Der PS1 triggert aktuell `python -m app.cli.main paper-trading cron` (oder ähnlich) alle 10 min + `daily-strategy bootstrap` ab 08:00. **Zu klären:** Gibt es einen CLI-Entry `paper-trading cron`? Wenn nein: neuen schreiben, der die relevanten PS1-Steps portiert.

### 3.2 Auf dem Pi — am 2026-05-01 vor Ort
- [ ] **OS-Check:** `lsb_release -a` (Debian 12 / Bookworm oder neuer).
- [ ] **Python 3.12+:** `python3 --version`. Falls < 3.12: via `apt` oder pyenv nachziehen. CLAUDE.md erzwingt 3.12+.
- [ ] **User anlegen:** `sudo adduser --disabled-password kai && sudo usermod -aG sudo kai`.
- [ ] **Repo klonen:** `sudo -u kai git clone <remote> /home/kai/ai_analyst_trading_bot`.
- [ ] **Deps:** `cd /home/kai/ai_analyst_trading_bot && python3 -m venv .venv && .venv/bin/pip install -U pip && .venv/bin/pip install -e .` (oder Variante je nach `pyproject.toml`).
- [ ] **.env deployen:** via `scp` vom Laptop (**nicht** Klartext in Chat/Git), Permissions `chmod 600 .env`, Owner `kai:kai`.
- [ ] **Cloudflared installieren:** ARM64-Build von `https://github.com/cloudflare/cloudflared/releases` (exakte URL bei Migration verifizieren, nicht jetzt raten). `cloudflared --version` muss laufen.
- [ ] **Cloudflared-Bundle deployen:** `~/.cloudflared/` anlegen, die 3 Files dort ablegen, `chmod 600`.
- [ ] **Artefakt-Tarball deployen:** `tar -xzf artifacts_2026-04-30.tar.gz` in `/home/kai/ai_analyst_trading_bot/`.
- [ ] **Logs-Verzeichnis:** `mkdir -p logs && chown kai:kai logs`.

### 3.3 systemd-Units schreiben (am Pi, nach Deps-Install)
- [ ] `/etc/systemd/system/kai-server.service` — siehe Template §6.1.
- [ ] `/etc/systemd/system/kai-agent-worker.service` — §6.2.
- [ ] `/etc/systemd/system/cloudflared.service` — §6.3.
- [ ] `/etc/systemd/system/kai-paper-trading.{service,timer}` — §6.4.
- [ ] `/etc/systemd/system/kai-daily-strategy.{service,timer}` — §6.5.
- [ ] `sudo systemctl daemon-reload && sudo systemctl enable --now kai-server kai-agent-worker cloudflared kai-paper-trading.timer kai-daily-strategy.timer`.

---

## 4. Secret-Inventar

Keys aus der aktuellen `.env` (Kategorien, Werte vertraulich — wandern via `scp`, nicht via Chat):

| Kategorie | Keys |
|---|---|
| Infrastruktur | `DB_URL`, `APP_API_KEY`, `CF_ACCESS_ALLOWED_EMAILS` |
| LLM-Provider | `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `OPENAI_MODEL`, `OPENAI_TIMEOUT`, `GEMINI_API_KEY` |
| Markt/News | `COINGECKO_API_KEY`, `BINANCE_ENABLED`, `X_BEARER_TOKEN`, `YOUTUBE_API_KEY`, `NEWSDATA_API_KEY` |
| Operator-Telegram | `OPERATOR_TELEGRAM_BOT_TOKEN`, `OPERATOR_ADMIN_CHAT_IDS`, `OPERATOR_TELEGRAM_POLLING_ENABLED`, `OPERATOR_TELEGRAM_DRY_RUN`, `OPERATOR_TELEGRAM_WEBHOOK_SECRET`, `OPERATOR_TELEGRAM_DASHBOARD_URL`, `OPERATOR_SIGNAL_*` (4 Keys) |
| Alert-Telegram | `ALERT_TELEGRAM_ENABLED`, `ALERT_TELEGRAM_TOKEN`, `ALERT_TELEGRAM_CHAT_ID` |
| Alert-Email | `ALERT_EMAIL_*` (7 Keys) |
| Alert-Global | `ALERT_DRY_RUN` |
| TradingView | `TRADINGVIEW_WEBHOOK_ENABLED`, `TRADINGVIEW_WEBHOOK_AUTH_MODE`, `TRADINGVIEW_WEBHOOK_SHARED_TOKEN`, `TRADINGVIEW_WEBHOOK_SIGNAL_ROUTING_ENABLED` |

**Risikoregel:** Nach dem `scp` die `.env` auf dem Laptop **nicht löschen** — Laptop bleibt Fallback, bis Pi 48 h stabil läuft.

---

## 5. DNS / Tunnel-Switchover

Der Named Tunnel `kai` ist domänengebunden, nicht hostgebunden — Cloudflare routet zum jeweils aktiven `cloudflared`-Prozess, der sich mit dieser Tunnel-UUID gegen Cloudflare authentifiziert. **Wenn sowohl Laptop als auch Pi gleichzeitig `cloudflared` mit derselben UUID fahren, verteilt Cloudflare die Verbindungen round-robin** — daher für einen sauberen Cutover:

1. Pi-Stack starten (alle Units enabled), Health lokal prüfen (`curl http://127.0.0.1:8000/health` auf dem Pi).
2. **Laptop:** `bash scripts/server_stop.sh` — stoppt Server + Tunnel + Worker.
3. Pi-Tunnel bleibt allein aktiv. `curl https://kai-trader.org/health` von extern (Handy ohne WLAN) muss grün sein.
4. BotFather Mini App URL ist bereits auf `https://kai-trader.org/dashboard/` konfiguriert — kein Update nötig (siehe Memory `reminder_cloudflare_named_tunnel.md`, offener Punkt war Stand 04-17 bereits BotFather-Umstellung; beim Pi-Switch prüfen ob erledigt).

**Rollback:** Pi-Stack stoppen (`systemctl stop kai-server cloudflared ...`), Laptop `bash scripts/server_start.sh` — binnen 60 s wieder online. Voraussetzung: Laptop-Repo und `.env` unverändert.

---

## 6. systemd-Templates (zum Anlegen am Pi)

### 6.1 `kai-server.service`
```ini
[Unit]
Description=KAI Pipeline Server (FastAPI + in-process schedulers)
After=network-online.target
Wants=network-online.target

[Service]
User=kai
Group=kai
WorkingDirectory=/home/kai/ai_analyst_trading_bot
EnvironmentFile=/home/kai/ai_analyst_trading_bot/.env
ExecStart=/home/kai/ai_analyst_trading_bot/.venv/bin/python -m uvicorn app.api.main:app --host 127.0.0.1 --port 8000 --log-level info
Restart=on-failure
RestartSec=10
StandardOutput=append:/home/kai/ai_analyst_trading_bot/logs/server.log
StandardError=append:/home/kai/ai_analyst_trading_bot/logs/server.log

[Install]
WantedBy=multi-user.target
```

### 6.2 `kai-agent-worker.service`
```ini
[Unit]
Description=KAI Agent Worker
After=kai-server.service
Requires=kai-server.service

[Service]
User=kai
Group=kai
WorkingDirectory=/home/kai/ai_analyst_trading_bot
EnvironmentFile=/home/kai/ai_analyst_trading_bot/.env
ExecStart=/home/kai/ai_analyst_trading_bot/.venv/bin/python -m app.agents.worker --loop
Restart=on-failure
RestartSec=15
Environment=PYTHONIOENCODING=utf-8
StandardOutput=append:/home/kai/ai_analyst_trading_bot/logs/agent_worker.log
StandardError=append:/home/kai/ai_analyst_trading_bot/logs/agent_worker.log

[Install]
WantedBy=multi-user.target
```
**Verifiziert 2026-04-20:** Worker-Modul = `app.agents.worker`, Flag `--loop` (Quelle: `scripts/agent_worker_start.sh:37`). `PYTHONIOENCODING=utf-8` wird im PS1-Pendant explizit gesetzt — hier über `Environment=` systemd-äquivalent.

### 6.3 `cloudflared.service`
```ini
[Unit]
Description=Cloudflare Tunnel (kai-trader.org)
After=network-online.target kai-server.service
Wants=network-online.target
Requires=kai-server.service

[Service]
User=kai
Group=kai
ExecStart=/usr/local/bin/cloudflared tunnel --config /home/kai/.cloudflared/config.yml run kai
Restart=always
RestartSec=10
StandardOutput=append:/home/kai/ai_analyst_trading_bot/logs/tunnel.log
StandardError=append:/home/kai/ai_analyst_trading_bot/logs/tunnel.log

[Install]
WantedBy=multi-user.target
```

### 6.4 `kai-paper-trading.{service,timer}`
```ini
# kai-paper-trading.service
[Unit]
Description=KAI Paper-Trading cron (10 min tick)
After=kai-server.service
Requires=kai-server.service

[Service]
Type=oneshot
User=kai
WorkingDirectory=/home/kai/ai_analyst_trading_bot
EnvironmentFile=/home/kai/ai_analyst_trading_bot/.env
Environment=PYTHON=/home/kai/ai_analyst_trading_bot/.venv/bin/python
ExecStart=/bin/bash /home/kai/ai_analyst_trading_bot/scripts/paper_trading_cron.sh
StandardOutput=journal
StandardError=journal
```
**Note:** Das Skript schreibt sein eigenes Log nach `artifacts/paper_trading_cron.log` (append-only, identisch zum Laptop-Pfad) — systemd-journal fängt nur ab, was darüber hinaus auf stdout/stderr landet.
```ini
# kai-paper-trading.timer
[Unit]
Description=Trigger KAI Paper-Trading every 10 min
Requires=kai-server.service

[Timer]
OnBootSec=2min
OnUnitActiveSec=10min
AccuracySec=30s
Unit=kai-paper-trading.service

[Install]
WantedBy=timers.target
```
**Verifiziert 2026-04-20:** Es gibt **keinen** CLI-Entry `paper-trading cron` — der PS1 orchestriert 12+ eigenständige CLI-Commands mit Countern/Markern (nicht 1:1 als Single-Command portierbar). Lösung: Bash-Port `scripts/paper_trading_cron.sh` mit funktionaler Parität zum PS1 (gleiche Counter-Dateien `.annotate_counter`/`.briefing_date`/`.daily_strategy_date`/`.pipeline_counter`/`.newsdata_counter`/`.youtube_counter`/`.twitter_counter`, gleiche CLI-Aufrufe, gleiches Log-Format). **Server-Watchdog entfällt**, da systemd `kai-server.service` mit `Restart=on-failure` diese Rolle übernimmt.

### 6.5 `kai-daily-strategy.{service,timer}`
```ini
# kai-daily-strategy.service
[Unit]
Description=KAI Daily Strategy Skeleton

[Service]
Type=oneshot
User=kai
WorkingDirectory=/home/kai/ai_analyst_trading_bot
EnvironmentFile=/home/kai/ai_analyst_trading_bot/.env
ExecStart=/home/kai/ai_analyst_trading_bot/.venv/bin/python -m app.cli.main daily-strategy bootstrap
```
```ini
# kai-daily-strategy.timer
[Unit]
Description=Daily strategy skeleton every morning 08:00 Europe/Berlin

[Timer]
OnCalendar=*-*-* 08:00:00 Europe/Berlin
Persistent=true
Unit=kai-daily-strategy.service

[Install]
WantedBy=timers.target
```
**Note:** `Persistent=true` holt den Trigger nach, wenn der Pi zum Zeitpunkt offline war — so keine „Lücken" im Daily-Artefakt.

---

## 7. Post-Migration-Validierung (Pflicht-Checks vor Laptop-Shutdown)

Nach dem Cutover in dieser Reihenfolge abhaken:

- [ ] `systemctl status kai-server kai-agent-worker cloudflared` → alle 3 `active (running)`.
- [ ] `systemctl list-timers kai-*` → beide Timer listed mit `next`-Zeit.
- [ ] Pi lokal: `curl -s http://127.0.0.1:8000/health | jq '.status'` → `"ok"`.
- [ ] Handy (Mobilfunk, nicht WLAN): `https://kai-trader.org/health` → HTTP 200.
- [ ] Telegram: `/status` an den KAI-Bot → neue Antwort (nicht zwischengespeicherter Laptop-State).
- [ ] TradingView: 1 Smoke-Webhook aus TV-Dashboard feuern → landet in `artifacts/tradingview_pending_signals.jsonl` auf dem **Pi**, nicht Laptop.
- [ ] Log-Freshness: `tail -f logs/server.log` zeigt laufende Events (Telegram-Poll, RSS-Scheduler).
- [ ] Daily-Strategy: am nächsten Morgen 08:05 — existiert `artifacts/daily_strategy/<YYYY-MM-DD>.md` auf dem Pi?
- [ ] PositionMonitor: in `logs/server.log` → `"event": "position_monitor_scheduler_started"` **und** periodische Ticks (`position_monitor_tick` o.ä.).
- [ ] Agent-Worker: Telegram-Paste-Test → Envelope-Antwort binnen 30 s.

**Erst wenn alle 10 Haken grün sind, darf der Laptop runter.**

---

## 8. Offene Pre-Flight-Fragen (vor dem 2026-05-01 klären)

1. ~~**CLI-Entry `paper-trading cron`**~~ — ✅ **geklärt 2026-04-20:** kein CLI-Entry nötig, Bash-Port `scripts/paper_trading_cron.sh` liefert funktionale Parität. Siehe §6.4.
2. ~~**Agent-Worker-Entrypoint**~~ — ✅ **geklärt 2026-04-20:** `python -m app.agents.worker --loop` (Quelle `scripts/agent_worker_start.sh:37`). In §6.2 eingetragen.
3. ~~**Ruft die CLI `daily-strategy bootstrap` ohne TTY sauber durch?**~~ ✅ **geklärt 2026-04-22 (V3-Dry-Run):** `python -m app.cli.main daily-strategy bootstrap --no-notify` läuft sauber, idempotent (bei vorhandener Datei: Exit-Status 0, Output `already present: artifacts/daily_strategy/<DATE>.md`). Kein TTY-Bedarf. Für systemd-Timer: `--no-notify` bewusst weglassen (Notify ist kostenfrei und gewünscht), nur falls Telegram-Token noch nicht gesetzt → `--no-notify`.
4. **Port 8000 auf Pi:** lokal frei? Falls Pi schon andere Services hat, `ss -tlnp | grep 8000` zeigt das.
5. **Zeitzone Pi:** `timedatectl` → `Europe/Berlin`. Sonst stimmen die Timer-Cron-Zeiten nicht, und die Daily-Artefakt-Namen (UTC-Date) driften vs. lokaler 08:00-Trigger.
6. **SSH-Zugriff ab Migration:** User ist am 05-01 vor Ort. Für Remote-Debugging ab 05-02 muss SSH-Port erreichbar sein (entweder LAN-only + VPN, oder Cloudflare-Tunnel-SSH). **Empfehlung:** Cloudflare-Tunnel-SSH (zero open ports im Router, gleiche Domain-Infra). Setup-Block siehe §8.6 unten.
7. **BotFather-Mini-App-URL:** prüfen ob bereits auf `https://kai-trader.org/dashboard/` (nicht die alte Trycloudflare-URL). War laut Memory `reminder_cloudflare_named_tunnel.md` am 04-17 noch offen. **User-Action:** @BotFather → `/mybots` → KAI-Bot → Bot Settings → Menu Button/Configure Mini App — aktuellen Wert melden, dann schließen wir den Reminder.

### 8.6 SSH-Tunnel-Setup (Cloudflare Access)

Konkretisierung der Empfehlung aus §8.6. Setup am 05-01 nach Cloudflared-Bring-up auf dem Pi.

**Pi: `~/.cloudflared/config.yml` erweitern** um SSH-Ingress:
```yaml
tunnel: beafc2ce-3c02-40c6-a6d7-359b2cc40cf6
credentials-file: /home/kai/.cloudflared/beafc2ce-3c02-40c6-a6d7-359b2cc40cf6.json

ingress:
  - hostname: kai-trader.org
    service: http://127.0.0.1:8000
  - hostname: ssh.kai-trader.org
    service: ssh://localhost:22
  - service: http_status:404
```
Danach `sudo systemctl restart cloudflared`.

**Cloudflare-DNS** (im Cloudflare-Dashboard, Zone `kai-trader.org`):
- CNAME `ssh.kai-trader.org` → `beafc2ce-3c02-40c6-a6d7-359b2cc40cf6.cfargotunnel.com` (Proxy on/orange-Wolke).

**Cloudflare Access** (Zero Trust → Access → Applications):
- Self-hosted Application für `ssh.kai-trader.org`, Policy: E-Mail-Allowlist (gleicher User wie für `/dashboard`-Login).

**Laptop (Client) `~/.ssh/config`:**
```
Host pi.kai-trader.org
  HostName ssh.kai-trader.org
  ProxyCommand cloudflared access ssh --hostname %h
  User kai
```

Test: `ssh pi.kai-trader.org` — Cloudflare prüft Identity, dann SSH zum Pi.

**Aufwand:** 15-20 min am 05-01. **P1**, nicht Cutover-blockierend, aber innerhalb 24 h fertigstellen — sonst kein Remote-Debug möglich.

---

## 9. Ehrliche Aufwandsschätzung

| Phase | Aufwand | Blocker |
|---|---|---|
| §3.1 (Laptop-Prep) | 1–2 h | `paper-trading cron`-Entry muss existieren |
| §3.2 (Pi-Setup inkl. Deps) | 2–3 h | erste `pip install` auf ARM64 kann Wheels neu bauen, 15–30 min zusätzlich |
| §3.3 (systemd-Units) | 30 min | §8.1 und §8.2 blockieren sonst |
| §5 (Cutover) | 15 min | Pi muss erstmal 10 min stabil laufen, sonst Rollback |
| §7 (Validierung) | 30 min aktiv + 24 h Beobachtung | Daily-Strategy-Check erst am nächsten Morgen verifizierbar |

**Gesamt Tag 05-01 vor Ort:** ~4–6 h realistisch. Plus 24–48 h Beobachtungsfenster bis Laptop-Abschaltung.

---

## 10. Rollback

Trigger: irgendein Check in §7 schlägt fehl, oder Pi crasht binnen 48 h mehr als 1×.

1. Auf dem Pi: `sudo systemctl stop kai-server kai-agent-worker cloudflared kai-paper-trading.timer kai-daily-strategy.timer`.
2. Auf dem Laptop: `bash scripts/server_start.sh`.
3. Cloudflare routet binnen ~60 s zurück zum Laptop-`cloudflared` (gleiche Tunnel-UUID).
4. `curl https://kai-trader.org/health` extern → muss wieder grün.
5. Fehler-RCA in separatem Doc, **nicht** erneut migrieren ohne Fix.

---

## 11. Dry-Run-Verifikation 2026-04-22 (V3)

Auf dem Laptop ohne Pi durchgeführt. Ziel: alle in §6 referenzierten Skripte/CLI-Pfade existieren und sind ausführbar; keine Überraschung am 05-01.

### 11.1 Skripte/Module verifiziert (file:line)
- `scripts/paper_trading_cron.sh` → existiert, Bash-Port mit funktionaler Parität zum PS1 ✓
- `scripts/agent_worker_start.sh:37` → `nohup python -m app.agents.worker --loop` ✓ (matched §6.2 ExecStart)
- `python -m app.cli.main daily-strategy bootstrap --no-notify` → idempotent, exit 0, kein TTY nötig ✓
- `app/cli/main.py:101` → `from app.cli.commands.daily_strategy import daily_strategy_app` ✓

### 11.2 ARM64-Wheel-Audit (alle Direct-Deps aus pyproject.toml)
Risiko: einige C-/Rust-Extensions brauchen aarch64-Wheels, sonst Build-from-Source (15-30 min, gcc + dev-headers nötig).

| Dep | ARM64-Wheel auf PyPI? | Risiko |
|---|---|---|
| `pydantic>=2.10` (Rust core) | ✓ | none |
| `psycopg2-binary>=2.9.10` | ✓ (manylinux_aarch64) | none |
| `asyncpg>=0.30` (C ext) | ✓ | none |
| `tiktoken>=0.8` (Rust) | ✓ | none |
| `selectolax>=0.3.21` (Lexbor C) | ✓ | none |
| `httpx`, `feedparser`, `beautifulsoup4`, `structlog`, `typer`, `rich`, `apscheduler`, `tenacity`, `pydantic-settings`, `sqlalchemy`, `alembic`, `aiosqlite`, `mcp` | pure-Python | none |
| `openai`, `anthropic`, `google-genai` | pure-Python | none |

→ **Erwartung:** `pip install -e .` auf Pi 4b (Bookworm/aarch64) zieht alles als Wheels, keine Compile-Zeit. Falls doch eine Quelle erscheint, ist das ein Hinweis auf eine inkompatible Python-Version (z.B. 3.11 statt 3.12) — vor `pip install` mit `python3 --version` prüfen.

### 11.3 Postgres-Migrationsweg
Aktuelle Strategie: pg_dump vom Laptop → Pi (siehe §3.1). Voraussetzung: gleiche Major-Version Postgres beidseitig. Auf dem Laptop:
```bash
psql -V    # Major-Version notieren
pg_dump --format=custom --file=kai_db_<DATE>.dump <db_url>
```
Auf dem Pi vor Restore: gleiche Major-Version installieren. **Vorab-Action für User:** Postgres-Major heute notieren, damit am 05-01 nicht das Risiko einer Cross-Version-Migration entsteht.

### 11.4 Open-Risk-Tabelle (Stand 2026-04-22, D-9)

| Risiko | Mitigation | Verantwortlich |
|---|---|---|
| Python 3.12 fehlt auf Pi-Image | `apt install python3.12 python3.12-venv` (Bookworm hat 3.11; ggf. deadsnakes oder pyenv nötig) | User vor Ort |
| Postgres-Major-Mismatch Laptop↔Pi | Heute Major auf Laptop notieren, gleiche Version auf Pi | User vor Ort |
| ARM64-Wheel überraschend nicht da | `pip install --only-binary=:all:` als Trockenlauf vor systemd-Enable | Pi-Session |
| Cloudflared-Bundle scp scheitert (`.cloudflared/` permissions) | Pi-User `kai` muss vor `scp` existieren, Zielpfad `/home/kai/.cloudflared/` mit `mkdir -p && chown kai:kai` | Pi-Session |
| Daily-Strategy-Telegram-Notify schickt aus Pi-Kontext | `--no-notify` für ersten Trigger, dann auf `notify` umstellen | systemd-Unit |
| BotFather-URL noch nicht umgestellt | siehe §8.7 — heute beim User explizit anstoßen | User |

### 11.5 Was diese Dry-Run NICHT klärt (echtes Pi-Setup nötig)
- Cloudflared-ARM64-Binary-Download — URL ändert sich pro Release, am 05-01 frisch holen
- systemd-Pfade: `/etc/systemd/system/...` — auf RaspiOS Bookworm Standard, aber je nach Image variabel
- `Environment=TZ=Europe/Berlin` ggf. zusätzlich zu `timedatectl` setzen wenn Locale-Issues auftreten
- Performance-Headroom Pi 4b unter realer 138-cycles/day-Last — erst Live messbar

---

## Status

- **Erstellt:** 2026-04-20
- **V3-Verifikation ergänzt:** 2026-04-22 (§11)
- **Verantwortlich:** Operator (User) + Claude-Session am 05-01
- **Nächste Review:** spätestens 2026-04-28 (D-3 vor Migration) — verbleibende Punkte in §8 (4, 5, 6, 7) prüfen
- **Quer-Referenz:** Memory `reminder_server_migration_pi.md`, `reminder_cloudflare_named_tunnel.md`
