# deploy/

Deployment-Artefakte für den Pi-Cutover (2026-05-01, D-7 vom 2026-04-24).

## `systemd/`

Sieben systemd-Units für den kompletten KAI-Stack auf dem Raspberry Pi 4b:

| Unit | Zweck | Restart |
|---|---|---|
| `kai-server.service` | FastAPI + in-process Scheduler (RSS, PositionMonitor) | `on-failure`, 10 s |
| `kai-agent-worker.service` | Agent-Queue-Worker (SENTR/Watchdog/Architect/DALI/Neo/SATOSHI) | `on-failure`, 15 s |
| `cloudflared.service` | Cloudflare Named Tunnel `kai-trader.org` | `always`, 10 s |
| `kai-paper-trading.service` + `.timer` | 10-min Paper-Trading-Cron (Bash-Port von `paper_trading_cron.ps1`) | Timer |
| `kai-daily-strategy.service` + `.timer` | Morning 08:00 Europe/Berlin Strategy-Skeleton | Timer |

Annahme: Checkout unter `/home/kai/ai_analyst_trading_bot`. Wenn woanders, Unit-Files vor Install editieren.

## Install

Auf dem Pi, **nach** `git clone`, `python -m venv .venv`, `pip install -e .`, `.env`-Transfer:

```bash
sudo bash scripts/pi_install_systemd.sh               # install + enable + start
sudo bash scripts/pi_install_systemd.sh --dry-run     # show what would happen
sudo bash scripts/pi_install_systemd.sh --uninstall   # stop + disable + remove
```

## Verifikation

Die 10 Haken aus `docs/pi_migration/preflight.md` §7 **vor** Laptop-Shutdown durchlaufen:

```bash
systemctl status kai-server kai-agent-worker cloudflared
systemctl list-timers 'kai-*'
curl -s http://127.0.0.1:8000/health | jq '.status'       # -> "ok"
```

Dann Remote: `https://kai-trader.org/health` vom Handy aus (Mobilfunk, nicht WLAN).

## Referenzen

- `docs/pi_migration/preflight.md` — volles Cutover-Runbook mit Checklist
- `DECISION_LOG.md` D-190 — Commit-Hintergrund
- `artifacts/agents/neo/findings.jsonl` NEO-F-META-20260424-005 — Befund der zur Repo-Aufnahme geführt hat

## Hardening-Notes

Die drei Long-Runner (`kai-server`, `kai-agent-worker`, `cloudflared`) haben `NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=strict`, `ProtectHome=read-only` gesetzt. Nur `/home/kai/ai_analyst_trading_bot` ist writable. Bei späterem Bedarf (z.B. Zugriff auf `/tmp/kai-*.sock`) `ReadWritePaths=` ergänzen, nicht die Hardening-Flags aufweichen.
