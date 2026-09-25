# deploy/

Deployment-Artefakte für den Pi-Cutover (2026-05-01, D-7 vom 2026-04-24).

## `systemd/`

Systemd-Units für den kompletten KAI-Stack auf dem Raspberry Pi 4b:

| Unit | Zweck | Restart |
|---|---|---|
| `kai-server.service` | FastAPI + in-process Scheduler (RSS, PositionMonitor) | `on-failure`, 10 s |
| `kai-agent-worker.service` | Agent-Queue-Worker (autonome Handler: SENTR/Watchdog/Architect — DALI/Neo/SATOSHI laufen interaktiv, s. `AGENTS.md` § Wiring-Realität) | `always`, 30 s |
| `kai-tg-listener.service` | Telegram Premium-Channel MTProto Listener | `always`, 30 s |
| `cloudflared.service` | Cloudflare Named Tunnel `kai-trader.org` | `always`, 10 s |
| `kai-paper-trading.service` + `.timer` | 10-min Paper-Trading-Cron (Bash-Port von `paper_trading_cron.ps1`) | Timer |
| `kai-daily-strategy.service` + `.timer` | Morning 08:00 Europe/Berlin Strategy-Skeleton | Timer |
| `kai-daily-strategy-reminder.service` + `.timer` | 12:00 Europe/Berlin Reminder falls Daily-Review leer/fehlt | Timer |
| `kai-pi-health.service` + `.timer` | 7d Post-Cutover Health-Digest | One-shot Timer |
| `kai-service-watchdog.service` + `.timer` | Externer 5-Minuten-Service-Watchdog mit Restart + Telegram-Alarm | Timer |

Annahme: Checkout unter `/home/kai/ai_analyst_trading_bot`. Wenn woanders, Unit-Files vor Install editieren.

## Install

Auf dem Pi, **nach** `git clone`, `python -m venv .venv`, deterministischem Lockfile-Install
(`.venv/bin/pip install --no-cache-dir -r requirements.lock` — Lock-File-Migration DS-20260527-V5,
NICHT `pip install -e .`; vollständiger Pfad in `docs/security/lock_file_workflow.md` § Pi-Production-Deploy),
`.env`-Transfer:

```bash
sudo bash scripts/pi_install_systemd.sh               # install + enable + start
sudo bash scripts/pi_install_systemd.sh --dry-run     # show what would happen
sudo bash scripts/pi_install_systemd.sh --uninstall   # stop + disable + remove
```

## Verifikation

Die 10 Haken aus `docs/pi_migration/preflight.md` §7 **vor** Laptop-Shutdown durchlaufen:

```bash
systemctl status kai-server kai-agent-worker kai-tg-listener cloudflared
systemctl list-timers 'kai-*'
curl -s http://127.0.0.1:8000/health | jq '.status'       # -> "ok"
journalctl -u kai-service-watchdog -n 30
```

Dann Remote: `https://kai-trader.org/health` vom Handy aus (Mobilfunk, nicht WLAN).

## Referenzen

- `docs/pi_migration/preflight.md` — volles Cutover-Runbook mit Checklist
- `DECISION_LOG.md` D-190 — Commit-Hintergrund
- `artifacts/agents/neo/findings.jsonl` NEO-F-META-20260424-005 — Befund der zur Repo-Aufnahme geführt hat

## Hardening-Notes

Die drei Long-Runner (`kai-server`, `kai-agent-worker`, `cloudflared`) haben `NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=strict`, `ProtectHome=read-only` gesetzt. Nur `/home/kai/ai_analyst_trading_bot` ist writable. Bei späterem Bedarf (z.B. Zugriff auf `/tmp/kai-*.sock`) `ReadWritePaths=` ergänzen, nicht die Hardening-Flags aufweichen.

## `needrestart/`

`50-kai.conf` verhindert, dass `needrestart` nach einem `unattended-upgrade` die
`kai-*`-Dienste neu startet (Vorfall 25.09.2026 04:34Z, mitten im Pilot D-281).
Die Dienste erscheinen dann unter "Service restarts being deferred" und laden die
neue Bibliothek mit dem nächsten Release-Restart.

Vor der Installation ohne Root prüfen, dass das Snippet parst und greift:

```bash
perl -e 'our %nrconf; my $b=do{local(@ARGV,$/)="/etc/needrestart/needrestart.conf";<>};
  $b =~ s/# Read additional config snippets.*//s; eval $b; die $@ if $@;
  eval do{local(@ARGV,$/)="deploy/needrestart/50-kai.conf";<>}; die $@ if $@;
  for my $u (qw(kai-server.service ssh.service)) { my $r=1;
    for my $re (keys %{$nrconf{override_rc}}) { next unless $u =~ /$re/; $r=$nrconf{override_rc}->{$re}; last }
    print "$u restart=$r\n" }'
# Soll: kai-server.service restart=0, ssh.service restart=1
sudo install -m 0644 -o root -g root deploy/needrestart/50-kai.conf /etc/needrestart/conf.d/50-kai.conf
```
