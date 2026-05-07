# Pi 5 Cutover Runbook

**Hardware:** Raspberry Pi 5, 16 GB RAM (Lieferung 2026-05-05/-06)
**Soll-Zustand:** Pi 5 ersetzt Pi 4b (kai-pi, 192.168.178.20, 894 MB RAM) als Source-of-Truth.
**Cutover-Fenster:** idealerweise 2026-05-07 bis 2026-05-10 (Wochentage, vor Re-Entry-Wochenende 2026-05-16).
**Risk-Level:** Mittel — Daten-Cutover (`dev.db` SQLite) ist klein, MTProto-Session ist die kritische Stelle, cloudflared-Tunnel switched automatisch.
**Erwartete Dauer:** 2-3 h sauber · 4-5 h mit Findings/Detours.
**Rollback-Ziel:** alter Pi bleibt 24 h online und kalt-startbar bis Pi-5-Stabilität bestätigt.

> **Authority:** Jeder Schritt ist Operator-Sign-off-pflichtig. Claude führt Read-only-Smokes selbständig, alle Mutations-Schritte (`systemctl stop`, `scp`, `git pull`) erfordern explizite OK.

---

## Phase 0 — Pre-Cutover Vorbereitung (am Vortag, T-1)

### 0.1 Snapshot Pi 4b State

```bash
# auf Laptop:
ssh ubuntu@192.168.178.20 'cd /home/ubuntu/ai_analyst_trading_bot && \
  echo "=== GIT HEAD ===" && git log --oneline -3 && \
  echo "=== AUDIT ===" && wc -l artifacts/paper_execution_audit.jsonl && \
  sha256sum artifacts/paper_execution_audit.jsonl && \
  echo "=== DB ===" && wc -c data/dev.db && sha256sum data/dev.db && \
  echo "=== MEM ===" && free -m && \
  echo "=== SERVICES ===" && \
  for s in kai-server kai-agent-worker kai-tg-listener cloudflared kai-service-watchdog.timer; do \
    printf "%-32s %s\n" $s $(systemctl is-active $s); done' \
  > artifacts/runbooks/pi4b_snapshot_$(date -u +%Y%m%dT%H%MZ).txt
```

**Artefakt-Pfad:** `artifacts/runbooks/pi4b_snapshot_<timestamp>.txt`

### 0.2 Pi 5 OS-Vorbereitung (während Pi 4b weiter läuft)

- **OS:** Ubuntu Server 24.04 LTS arm64 (oder neuere LTS falls verfügbar) auf SD-Karte mit Pi Imager.
- **Hostname:** `kai-pi5` (NICHT `kai-pi` — verhindert FritzBox-DNS-Konflikt während Parallelbetrieb).
- **User:** `ubuntu` (gleicher Name wie Pi 4b — vereinfacht Service-Unit-Files).
- **SSH-Key:** Laptop-Public-Key in `~/.ssh/authorized_keys` einspielen vor erstem Boot.

```bash
# einmalig auf Laptop:
ssh-copy-id ubuntu@<pi5-temp-ip>  # erste IP aus FritzBox-DHCP
```

### 0.3 Findings aus 2026-05-01-Cutover (per Memory `project_pi_migration_progress.md`)

| # | Finding | Maßnahme für Pi 5 |
|---|---|---|
| 1 | telethon fehlte in pyproject.toml | ✅ resolved (`telethon>=1.43.0` ist offizielle Dep) — kein Workaround mehr nötig |
| 2 | systemd-Logfiles initial root-owned | `LogsDirectory=` in Unit-Files setzen statt `StandardOutput=append:` ODER `ExecStartPre=` mit chown belassen (jetzt schon im Unit-File) |
| 3 | `pi_install_systemd.sh` Pfadprüfung blockt SSH-non-interactive | `--force` Flag oder ENV-skip in Skript einbauen vor Pi-5-Run |
| 4 | TG-Listener-Unit fehlte komplett | ✅ resolved (e1c7fc5) |
| 5 | Alle Units hatten User=kai (nicht existent) | ✅ resolved (User=ubuntu) |

→ Vor Pi-5-Start: Finding #2 und #3 in einem Commit clean machen (lokal Repo, dann am Pi 5 frischer Pull).

### 0.4 Pi-5-spezifische Hygiene-Items

- **Symlink `/home/kai → /home/ubuntu` NICHT mitnehmen.** Service-Unit-Files lokal updaten auf direkte `/home/ubuntu/...`-Pfade. Spart eine Pfad-Indirektion und ist sauberer Migrations-Endzustand.
- **Log-Rotation für `logs/telegram_listener.err.log`** (Pi 4b 2026-05-05: 104 MB nicht-rotierend, durch lsof bestätigt) einrichten via `logrotate`-Config. Pfad ist `logs/`, **nicht** `artifacts/` — die echten Log-Files (server.log, server.err.log, telegram_listener.log, telegram_listener.err.log) liegen in `logs/`.

  ~~~
  # /etc/logrotate.d/kai
  /home/ubuntu/ai_analyst_trading_bot/logs/*.log {
      daily
      rotate 14
      compress
      delaycompress
      missingok
      notifempty
      copytruncate
      su ubuntu ubuntu
  }
  ~~~

  Smoke nach Install: `sudo logrotate -d /etc/logrotate.d/kai` (dry-run, listet was rotiert würde).

  > **Warum `copytruncate`:** kai-server (uvicorn) und kai-tg-listener halten ihre `.log`/`.err.log`-Files dauerhaft im write-Mode offen. Ohne `copytruncate` müsste der Service nach Rotation neu gestartet werden, um auf das neue File zu schreiben — das öffnet dieselbe Approval-Lücke, die wir mit der Watchdog-Probe minimieren. `copytruncate` kopiert den Inhalt weg und truncated das Original an Ort und Stelle, damit die offene File-Handle weiter funktioniert.
- **PG18 NICHT installieren** — KAI nutzt SQLite, Postgres ist ungenutzt, spart Pi-5-Ressourcen.

---

## Phase 1 — Pi 5 Hardware-Bootstrap (T-Day, 1-2 h vor Cutover)

### 1.1 Erste SSH-Verbindung Pi 5

```bash
ssh ubuntu@<pi5-temp-ip>  # IP aus FritzBox
sudo apt update && sudo apt upgrade -y
sudo apt install -y git curl python3.12 python3.12-venv build-essential
```

**NTP-Smoke (Pflicht — TV-Webhook Strict-Mode hat Skew-Limit `webhook_strict_ts_skew_seconds=300` aus settings.py:365):**

```bash
ssh ubuntu@<pi5-temp-ip> 'timedatectl status | grep -E "synchronized|NTP service|Time zone"'
# Erwartet: "System clock synchronized: yes" + "NTP service: active" + "Time zone: UTC" (oder Europe/Berlin, hauptsache stabil)
# Wenn nicht synchronized:
#   sudo timedatectl set-ntp true
#   sudo timedatectl set-timezone UTC
#   timedatectl status   # nochmal prüfen
```

> **Warum UTC:** AppSettings.runtime_config schreibt `timezone_internal: "UTC"` fest (settings.py:749). Pi-Local-Time anders zu setzen führt zu konfusen Timestamps in JSONL-Audits, aber bricht nichts. UTC ist die saubere Konvention.

### 1.2 cloudflared-Repo + tunnel-Connector

```bash
# auf Pi 5:
sudo mkdir -p --mode=0755 /usr/share/keyrings
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared $(lsb_release -cs) main" | sudo tee /etc/apt/sources.list.d/cloudflared.list
sudo apt update && sudo apt install -y cloudflared

# Tunnel-Credentials von Pi 4b kopieren:
mkdir -p ~/.cloudflared
scp ubuntu@192.168.178.20:/home/ubuntu/.cloudflared/*.json ~/.cloudflared/
scp ubuntu@192.168.178.20:/etc/cloudflared/config.yml ~/cloudflared-config.yml
sudo mv ~/cloudflared-config.yml /etc/cloudflared/config.yml

# Permissions als Default (NICHT erst in F2 als Recovery):
chmod 600 ~/.cloudflared/*.json
sudo chmod 600 /etc/cloudflared/config.yml

# Service installieren:
sudo cloudflared service install
```

> **Tunnel-Mode-Konsistenz:** Der bestehende Pi-4b-Tunnel ist credentials-file-basiert (`*.json` mit Tunnel-UUID). `cloudflared service install` darf den Mode nicht ändern. Wenn der `service install`-Output einen anderen Mode (z. B. tunnel-token) anbietet, **abbrechen** und Operator informieren — Tunnel-UUID muss dieselbe bleiben, sonst routet kai-trader.org ins Leere.

### 1.3 uv-Installation + Python-Stack

> **Branch-Strategie (Pflicht-Entscheidung VOR clone):** Der Pi 4b-State zum Cutover-Zeitpunkt ist HEAD `89f4061` plus dirty Working-Tree (siehe `pi5_state_audit_20260505.md` § Git-State). Lokal liegt `5614eca` auf `claude/p7/reentry-ia-codex-cycle`. Pi 5 darf **nicht blind** auf default-Branch landen, sonst läuft hardware-frisch, aber funktional ≠ Pi 4b.
>
> **Empfohlene Sequenz (sauber):**
> 1. Lokal sicherstellen: alle für Pi-4b nötigen Hotfixes sind als reguläre Commits auf `claude/p7/reentry-ia-codex-cycle` gepusht.
> 2. Pi-Patch `pi4b_dirty_patch_20260505.patch` reviewen → entweder schon in `5614eca`-Range enthalten (dann verworfen) oder als zusätzliche Commits gepusht.
> 3. Pi 5 klont und checkt **explizit** den Branch aus (`-b claude/p7/reentry-ia-codex-cycle`).
>
> **Notfall-Sequenz (Patch-Apply, nur wenn 1+2 nicht rechtzeitig):**
> Pi 5 klont default, holt `pi4b_dirty_patch_20260505.patch` per scp und applied lokal. Hinterlässt aber dirty Tree → reproduzierbar nur via Patch-Hash. Soll-Output als Audit-Spur in `artifacts/runbooks/pi5_patch_applied_<date>.txt` ablegen.

```bash
# auf Pi 5:
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.bashrc
mkdir -p /home/ubuntu/ai_analyst_trading_bot
cd /home/ubuntu/ai_analyst_trading_bot
# Saubere Sequenz — explizit den Operator-Branch:
git clone -b claude/p7/reentry-ia-codex-cycle https://github.com/<org>/ai_analyst_trading_bot.git .
git rev-parse HEAD  # protokollieren — soll == lokalem HEAD am Laptop sein
uv venv .venv --python 3.12
source .venv/bin/activate
uv pip install -e .

# 2026-05-07 Cutover-Lehre B-3: kai-server-Erststart crasht auf Blank-Slate
# mit `Failed to set up standard output: No such file or directory` —
# systemd oeffnet StandardOutput=append: VOR ExecStartPre, das mkdir
# kommt zu spaet. scripts/pi_install_systemd.sh + deploy/tmpfiles/kai.conf
# automatisieren das ueber systemd-tmpfiles-setup; bei Manual-Bypass
# (wie 2026-05-07) explizit:
mkdir -p logs/ && chown -R ubuntu:ubuntu logs/
```

**Notfall: Patch-Apply (nur wenn Hotfixes noch nicht gepusht):**

```bash
# auf Laptop:
scp ubuntu@192.168.178.20:/home/ubuntu/ai_analyst_trading_bot/artifacts/runbooks/pi4b_dirty_patch_20260505.patch /tmp/
scp /tmp/pi4b_dirty_patch_20260505.patch $PI5:/tmp/

# auf Pi 5:
cd /home/ubuntu/ai_analyst_trading_bot
git apply --check /tmp/pi4b_dirty_patch_20260505.patch  # erst dry-check
git apply /tmp/pi4b_dirty_patch_20260505.patch
git status > artifacts/runbooks/pi5_patch_applied_$(date -u +%Y%m%dT%H%MZ).txt
sha256sum /tmp/pi4b_dirty_patch_20260505.patch >> artifacts/runbooks/pi5_patch_applied_$(date -u +%Y%m%dT%H%MZ).txt
```

> **NICHT am Pi 4b** `uv pip install -e .` machen. Pi 5 hat 16 GB RAM, da darf der Build laufen.

---

## Phase 2 — Daten-Cutover (Critical Section, ~30 Min)

> **Diese Phase MUSS sequenziell.** Telegram-Session ist single-instance — wenn beide Pis gleichzeitig connecten, AuthKeyDuplicated-Storm.

### 2.1 Operator-Daten-Freeze ankündigen

Telegram-Push an Operator: "Pi-Cutover beginnt — keine neuen B-6-Approvals für nächste 30 Min."

### 2.2 Pi 4b Services stoppen (sequentiell)

```bash
# auf Laptop, einzeilig:
ssh ubuntu@192.168.178.20 'sudo systemctl stop kai-tg-listener kai-paper-trading.timer kai-service-watchdog.timer kai-agent-worker kai-server'

# Smoke: alles inactive?
ssh ubuntu@192.168.178.20 'for s in kai-server kai-agent-worker kai-tg-listener cloudflared kai-service-watchdog.timer kai-paper-trading.timer; do printf "%-32s %s\n" $s $(systemctl is-active $s); done'
```

> **2026-05-07 Cutover-Lehre B-2 — cloudflared zieht via `Requires=` automatisch mit:** `deploy/systemd/cloudflared.service` hat `Requires=kai-server.service`. Beim `systemctl stop kai-server` propagiert systemd den Stop auch an cloudflared. Die Annahme „cloudflared bleibt active bis Phase 3.5" stimmt damit faktisch nicht — Tunnel-Downtime startet hier. Konsequenz fuer Phase 2.1: Operator-Push-Fenster auf >= 30 min auslegen, sonst reisst er. Phase 3.5 muss nur noch Pi 5 cloudflared starten (Pi 4b ist schon down).

### 2.3 MTProto-Session-Logout am Pi 4b (gegen AuthKeyDuplicated-Storm)

> **Variante A (bevorzugt — robust):** Kleines Skript `scripts/telegram_logout.py` einchecken (12 Zeilen, liest API-ID/HASH aus `.env` via Settings, ruft `c.log_out()`). Spart Heredoc-Quoting durch zwei SSH-Layer. Memory `feedback_pi_remote_edits.md` hat genau diese Lehre.
>
> **Variante B (nur wenn Skript noch nicht existiert):** Inline-Python wie unten, aber **nur** wenn `TELEGRAM_API_ID/HASH` im Pi-4b-Shell-Env exportiert sind. Wenn Werte nur in `.env` stehen (Standard), schlägt das Inline-Kommando still fehl ("os.environ[...]" KeyError) — dann **muss** Variante A her.

**Variante A (Skript):**

```bash
# auf Laptop, einmalig: scripts/telegram_logout.py ins Repo committen + pushen.
# Dann am Pi 4b:
ssh ubuntu@192.168.178.20 'cd /home/ubuntu/ai_analyst_trading_bot && \
  git pull --ff-only && \
  source .venv/bin/activate && \
  python scripts/telegram_logout.py'
# Erwartet: "logged out" auf stdout.
```

**Variante B (Inline-Fallback, fragil):**

```bash
# auf Pi 4b — NUR wenn API-ID/HASH im Shell-Env stehen:
ssh ubuntu@192.168.178.20 'cd /home/ubuntu/ai_analyst_trading_bot && \
  source .venv/bin/activate && \
  set -a && source .env && set +a && \
  python -c "from telethon import TelegramClient; \
             import os; \
             c = TelegramClient(\"artifacts/telegram_channel.session\", api_id=int(os.environ[\"INGESTION_TELEGRAM_CHANNEL_API_ID\"]), api_hash=os.environ[\"INGESTION_TELEGRAM_CHANNEL_API_HASH\"]); \
             c.start(); c.log_out(); print(\"logged out\")"'
```

> **Hinweis Pfad:** Die Session-Datei liegt laut Codex-Settings unter `artifacts/telegram_channel.session` (settings.py:454, `INGESTION_TELEGRAM_CHANNEL_SESSION_PATH`), nicht `data/telegram_session`. Variante A liest den Pfad aus den Settings — Variante B muss er hier explizit reingeschrieben werden, sonst loggt das Skript eine andere (leere) Session aus.

> **5 Min Wartezeit** danach (Telegram-Server-Side-Cleanup). NICHT skippen — verhindert die `AuthKeyDuplicatedError`-Storm-Lehre aus 2026-05-02 02:55 UTC.

### 2.4 SQLite + Session-File + .env + **Listener-State-Files** scp zu Pi 5

> **Lehre 2026-05-04 (Cutover-Postmortem):** Der 2026-05-02-Cutover hat den Telegram-Listener-Checkpoint NICHT mit übertragen. Folge: 4 Tage Stille im Approval-Loop. Plus Code-Bug F6 (Chat-ID-Key-Asymmetrie) machte Recovery zusätzlich brüchig. Beide State-Files MÜSSEN beim Pi-5-Cutover dabei sein.

> **Lehre 2026-05-05 (V25-D-Audit):** Über die Listener-State-Files hinaus existieren weitere kritische JSONLs und State-DBs, die bei Cutover oft vergessen werden — Decision-Journal, Approval-Sends, Replay-Marker, persistenter TV-Replay-Cache. Wenn `tradingview_replay_cache.db` (D-189) fehlt und `TRADINGVIEW_WEBHOOK_REPLAY_CACHE_PERSISTENT=true` in `.env` aktiv ist, ist der Replay-Schutz nach Pi-5-Boot **neu** = Sicherheits-Regression im Webhook-Pfad. Wenn `operator_commands.jsonl` fehlt, fehlt der Decision-Journal-Tail = Audit-Lücke.

#### 2.4.0 SQLite WAL-Checkpoint (Pflicht VOR scp)

```bash
# auf Pi 4b — bringt offene WAL-Pages in die main DB-Datei zurück, leert WAL/SHM:
ssh $PI4B 'cd /home/ubuntu/ai_analyst_trading_bot && \
  sqlite3 data/dev.db "PRAGMA wal_checkpoint(TRUNCATE);" && \
  ls -la data/dev.db data/dev.db-wal data/dev.db-shm 2>/dev/null'
# Erwartet: data/dev.db-wal Größe = 0 (oder file existiert nicht mehr).
# Wenn WAL > 0: Service stoppte nicht sauber — zurück zu Phase 2.2 und kai-server hart killen, dann nochmal.
```

> **Warum:** Ohne Checkpoint kopiert `scp data/dev.db` nur den Main-Body — die letzten Transaktionen sitzen noch im WAL und gehen ohne Mit-Kopie verloren. Mit Checkpoint sind alle Pages in `data/dev.db` integriert; WAL/SHM müssen dann nicht transferiert werden.

#### 2.4.1 Hash-Snapshot Pi 4b (alle kritischen Files)

```bash
# auf Laptop:
PI4B=ubuntu@192.168.178.20
PI5=ubuntu@<pi5-temp-ip>

# Pflicht-Files (existieren immer).
# 2026-05-07 Cutover-Lehre B-1: artifacts/telegram_channel.session ist bewusst
# NICHT in dieser Liste — Phase 2.3 `client.log_out()` zerstoert die lokale
# Session-Datei (telethon-Default), und ein server-side invalidierter AuthKey
# macht eine kopierte Session-Datei sowieso wertlos. Re-Auth in Phase 2.7.
ssh $PI4B 'cd /home/ubuntu/ai_analyst_trading_bot && \
  sha256sum \
    data/dev.db \
    artifacts/telegram_channel_checkpoint.json \
    artifacts/telegram_listener_heartbeat \
    artifacts/.telegram_channel_replay.json \
    .env \
    artifacts/paper_execution_audit.jsonl \
    artifacts/telegram_message_envelope.jsonl \
    artifacts/telegram_channel_raw.jsonl \
    artifacts/operator_commands.jsonl \
    artifacts/telegram_signal_handoff.jsonl \
    artifacts/telegram_approval_send.jsonl' > /tmp/pi4b_hashes.txt

# Bedingte Files — nur wenn aktiv (D-189 Persistent Replay Cache):
ssh $PI4B 'cd /home/ubuntu/ai_analyst_trading_bot && \
  if grep -q "^TRADINGVIEW_WEBHOOK_REPLAY_CACHE_PERSISTENT=true" .env 2>/dev/null && \
     [ -f artifacts/tradingview_replay_cache.db ]; then \
    echo "PERSISTENT_REPLAY_CACHE_ACTIVE=1"; \
    sha256sum artifacts/tradingview_replay_cache.db; \
  else \
    echo "PERSISTENT_REPLAY_CACHE_ACTIVE=0"; \
  fi' >> /tmp/pi4b_hashes.txt

cat /tmp/pi4b_hashes.txt  # Operator-Sichtprüfung
```

#### 2.4.2 Files vom Pi 4b nach Laptop holen

```bash
# Pflicht-Files (Session-File raus — siehe B-1 Hinweis oben):
for f in \
  data/dev.db \
  artifacts/telegram_channel_checkpoint.json \
  artifacts/telegram_listener_heartbeat \
  artifacts/.telegram_channel_replay.json \
  .env \
  artifacts/paper_execution_audit.jsonl \
  artifacts/telegram_message_envelope.jsonl \
  artifacts/telegram_channel_raw.jsonl \
  artifacts/operator_commands.jsonl \
  artifacts/telegram_signal_handoff.jsonl \
  artifacts/telegram_approval_send.jsonl
do
  mkdir -p "/tmp/pi4b/$(dirname "$f")"
  scp "$PI4B:/home/ubuntu/ai_analyst_trading_bot/$f" "/tmp/pi4b/$f"
done

# Bedingt — Persistent Replay Cache DB (D-189):
if grep -q "PERSISTENT_REPLAY_CACHE_ACTIVE=1" /tmp/pi4b_hashes.txt; then
  scp "$PI4B:/home/ubuntu/ai_analyst_trading_bot/artifacts/tradingview_replay_cache.db" \
      "/tmp/pi4b/artifacts/tradingview_replay_cache.db"
fi
```

#### 2.4.3 Files zu Pi 5

```bash
# Zielordner sicherstellen:
ssh $PI5 'mkdir -p /home/ubuntu/ai_analyst_trading_bot/data /home/ubuntu/ai_analyst_trading_bot/artifacts'

# Pflicht-Files (Session-File raus — siehe B-1):
for f in \
  data/dev.db \
  artifacts/telegram_channel_checkpoint.json \
  artifacts/telegram_listener_heartbeat \
  artifacts/.telegram_channel_replay.json \
  .env \
  artifacts/paper_execution_audit.jsonl \
  artifacts/telegram_message_envelope.jsonl \
  artifacts/telegram_channel_raw.jsonl \
  artifacts/operator_commands.jsonl \
  artifacts/telegram_signal_handoff.jsonl \
  artifacts/telegram_approval_send.jsonl
do
  scp "/tmp/pi4b/$f" "$PI5:/home/ubuntu/ai_analyst_trading_bot/$f"
done

# Bedingt — Persistent Replay Cache DB:
if [ -f "/tmp/pi4b/artifacts/tradingview_replay_cache.db" ]; then
  scp "/tmp/pi4b/artifacts/tradingview_replay_cache.db" \
      "$PI5:/home/ubuntu/ai_analyst_trading_bot/artifacts/tradingview_replay_cache.db"
fi
```

#### 2.4.4 Permissions als Default (NICHT erst in F2 als Recovery)

```bash
ssh $PI5 'cd /home/ubuntu/ai_analyst_trading_bot && \
  chmod 600 .env && \
  chown -R ubuntu:ubuntu data/ artifacts/ .env && \
  ls -la .env data/dev.db | head'
```

#### 2.4.5 Hash-Verify auf Pi 5

```bash
ssh $PI5 'cd /home/ubuntu/ai_analyst_trading_bot && \
  sha256sum \
    data/dev.db \
    artifacts/telegram_channel_checkpoint.json \
    artifacts/telegram_listener_heartbeat \
    artifacts/.telegram_channel_replay.json \
    .env \
    artifacts/paper_execution_audit.jsonl \
    artifacts/telegram_message_envelope.jsonl \
    artifacts/telegram_channel_raw.jsonl \
    artifacts/operator_commands.jsonl \
    artifacts/telegram_signal_handoff.jsonl \
    artifacts/telegram_approval_send.jsonl'

# Wenn Replay-Cache-DB übertragen wurde:
ssh $PI5 'test -f /home/ubuntu/ai_analyst_trading_bot/artifacts/tradingview_replay_cache.db && \
  sha256sum /home/ubuntu/ai_analyst_trading_bot/artifacts/tradingview_replay_cache.db'

# Vergleich mit /tmp/pi4b_hashes.txt — MÜSSEN identisch sein.
diff <(sort /tmp/pi4b_hashes.txt | grep -v "ACTIVE=") \
     <(ssh $PI5 'cd /home/ubuntu/ai_analyst_trading_bot && \
                  sha256sum data/dev.db artifacts/* .env 2>/dev/null | sort')
# Erwartet: leerer Diff für die transferierten Files.
```

**Pflicht-Smoke unmittelbar nach Pi-5-Listener-Start:**
```bash
ssh $PI5 'cat /home/ubuntu/ai_analyst_trading_bot/artifacts/.telegram_channel_replay.json'
# Erwartet: scanned >= 0, processed >= 0, skipped_no_checkpoint = 0
# Wenn skipped_no_checkpoint = 1 → F6-Bug (Chat-ID-Key-Asymmetrie) ist nicht gefixt; siehe Listener-Reactivity-Followup
```

### 2.5 systemd-Units installieren

```bash
ssh $PI5 'cd /home/ubuntu/ai_analyst_trading_bot && \
  sudo bash deploy/systemd/pi_install_systemd.sh'

# Watchdog explizit:
ssh $PI5 'sudo systemctl enable --now kai-service-watchdog.timer'
```

### 2.6 Pre-Flight Settings-Validator-Smoke (Pflicht VOR Service-Start)

Greift Boot-Validatoren ab, bevor `systemctl start` sie als opaken `failed`-State serviert. Spart Diagnose-Zeit, wenn die Pi-4b-`.env` Werte enthält, die der Pi-5-Boot rejected.

```bash
ssh $PI5 'cd /home/ubuntu/ai_analyst_trading_bot && \
  source .venv/bin/activate && \
  python -c "from app.core.settings import get_settings; \
             s = get_settings(); \
             print(f\"OK env={s.env} bind={s.api_bind_host} re_entry={s.re_entry_mode.enabled} \
replay_persistent={s.tradingview.webhook_replay_cache_persistent} \
replay_db={s.tradingview.webhook_replay_cache_db_path}\")"'
```

**Erwartet:** Eine Zeile, beginnend mit `OK env=...`. Stack-Trace = Validator hat geblockt.

**Failure-Branches:**

| Symptom | Validator | Code-Stelle | Korrektur |
|---|---|---|---|
| `ConfigurationError: APP_API_BIND_HOST=... is not loopback` | NEO-P-001 B | `settings.py:627-652` | In `.env`: `APP_API_BIND_HOST=127.0.0.1` setzen ODER `APP_ALLOW_NON_LOOPBACK_BIND=1` explizit dokumentieren (nur wenn downstream-Firewall existiert) |
| `ConfigurationError: Re-entry invariants violated: ALERT_PROVENANCE_SECRET is empty` | D-202 S-001 | `settings.py:675-679` | `ALERT_PROVENANCE_SECRET` in `.env` setzen ODER `RE_ENTRY_MODE_ENFORCE_PROVENANCE_SECRET=0` |
| `... TRADINGVIEW_WEBHOOK_REPLAY_CACHE_PERSISTENT=false` | D-202 S-002a | `settings.py:682-690` | Persistent-Cache aktivieren ODER Enforce-Flag aus |
| `... is not absolute` | D-202 S-002b | `settings.py:693-701` | `TRADINGVIEW_WEBHOOK_REPLAY_CACHE_DB_PATH` auf absoluten Pfad setzen, z. B. `/home/ubuntu/ai_analyst_trading_bot/artifacts/tradingview_replay_cache.db` |
| `... INGESTION_TELEGRAM_CHANNEL_HEARTBEAT_PATH is empty` | D-202 S-003 | `settings.py:704-710` | Heartbeat-Pfad in `.env` setzen |
| `... B-002 ... not yet implemented` | D-202 B-002 | `settings.py:712-725` | `RE_ENTRY_MODE_ENFORCE_OBSERVABILITY_COMPLETE=0` (B-002 ist noch offen, siehe Memory `kai_listener_resilience_v25d_status.md`) |

> **Im Cutover-Fenster keine Re-Entry-Mode-Flags neu aktivieren.** Wenn Pi 4b ohne Re-Entry-Mode lief und die Migration auf Pi 5 nur Hardware-Wechsel ist, bleibt das so. Re-Entry-Aktivierung ist eigene D-202-Schiene mit eigener Pre-Flight-Checkliste.

### 2.7 Interaktive Telegram-Re-Auth auf Pi 5 (Pflicht — Operator-Aktion)

> **2026-05-07 Cutover-Lehre B-1:** Phase 2.3 `client.log_out()` zerstoert die lokale Session-Datei (`artifacts/telegram_channel.session`) als telethon-Default. Plus: ein server-side invalidierter AuthKey + clientseitige Session = `AuthKeyUnregisteredError` beim ersten Connect. Kopieren ist kein Fix, **interaktive Re-Auth auf Pi 5** ist die einzige korrekte Sequenz.

**Operator-Aktion (eigenes Terminal noetig — telethon-Prompts brauchen TTY):**

```bash
ssh ubuntu@<pi5-ip>
cd /home/ubuntu/ai_analyst_trading_bot
source .venv/bin/activate
python -c "import asyncio; from app.ingestion.telegram_channel_worker import setup_auth; asyncio.run(setup_auth())"
```

Drei interaktive Prompts:
1. `Please enter your phone (or bot token):` → `+49…` (gleiche Nummer wie Pi-4b-Setup; **`+`-Prefix Pflicht**, ohne Prefix nimmt Telegram die Eingabe nicht an).
2. `Please enter the code you received:` → Code aus Telegram-App-Push (oder SMS-Fallback). Bei Tippfehler erlaubt Telegram **maximal ~3 Code-Versuche** pro Login-Anlauf — danach Code-Reset noetig (neuer Aufruf von `setup_auth()`).
3. `Please enter your password:` → Cloud-Password (2FA), falls am Account aktiv.

**Erfolgs-Indikator:** Zeile `Signed in successfully as <Name>; remember to not break the ToS or you will risk an account ban!` plus Logger-Info `[channel-worker] auth ok: user_id=… username=…`. Danach existiert `artifacts/telegram_channel.session` (~28 KB).

**Smoke nach Auth:**
```bash
ssh $PI5 'ls -la /home/ubuntu/ai_analyst_trading_bot/artifacts/telegram_channel.session'
# Erwartet: ~28 KB, frisch erstellt (mtime = jetzt).
```

> **Sicherheits-Hinweis:** Diese Phase ist die **einzige Stelle** im Cutover, an der ein neuer AuthKey ueber den Wire geht. Phone+Code+Password muessen vom Operator persoenlich eingegeben werden — **nicht** ueber Claude/Codex/Subagent gepiped. Die neue Session-Datei `artifacts/telegram_channel.session` darf nicht in Logs, Audits oder Memory landen.

> **Failure-Branch:** Wenn `setup_auth()` mit `FloodWaitError` abbricht (z. B. wegen wiederholter falscher Codes), Wartezeit aus dem Telegram-Error abwarten und Aufruf wiederholen. Bei `PhoneNumberInvalidError` Phone-Number-Format pruefen (`+`-Prefix Pflicht).

---

## Phase 3 — Pi 5 Service-Start + Smoke (15-30 Min)

### 3.1 Sequenzieller Service-Start (NIE parallel)

```bash
ssh $PI5 'sudo systemctl start kai-server' && sleep 5
ssh $PI5 'sudo systemctl start kai-agent-worker' && sleep 3
ssh $PI5 'sudo systemctl start kai-paper-trading.timer' && sleep 3
ssh $PI5 'sudo systemctl start kai-tg-listener' && sleep 5
```

### 3.2 4-Service + Watchdog-Smoke (Maintenance-Restart-Protokoll)

```bash
ssh $PI5 'for s in kai-server kai-agent-worker kai-tg-listener cloudflared kai-service-watchdog.timer kai-paper-trading.timer; do printf "%-32s %s\n" $s $(systemctl is-active $s); done'
```

**Erwartung:** alle 6 = `active`. Wenn ein Service `failed`: `journalctl -u <unit> --no-pager -n 50` für Diagnose.

### 3.3 TCP-Verify Telegram-MTProto

```bash
ssh $PI5 'sudo ss -tnp | grep -E "149.154|telegram"'
```

**Erwartung:** ESTABLISHED-Verbindung zu `149.154.167.51:443` (oder analog `.45/.50`).

### 3.4 Audit-Continuity-Verify

```bash
ssh $PI5 'wc -l /home/ubuntu/ai_analyst_trading_bot/artifacts/paper_execution_audit.jsonl && \
  sha256sum /home/ubuntu/ai_analyst_trading_bot/artifacts/paper_execution_audit.jsonl'
```

**Erwartung:** Zeilen-Count + sha256 = exakt Pi 4b vor Stop. Kein Drift.

### 3.5 Tunnel-Connector-Switch zu Pi 5

```bash
# Pi 4b cloudflared stoppen — erst JETZT:
ssh $PI4B 'sudo systemctl stop cloudflared'

# Pi 5 cloudflared starten:
ssh $PI5 'sudo systemctl start cloudflared'

# Browser-Smoke vom Laptop:
curl -I https://kai-trader.org/dashboard/
# erwartet: HTTP/2 302 (Cloudflare Access redirect)
```

### 3.6 Operator-Smoke

Operator öffnet `https://kai-trader.org/dashboard/` im Browser → Cloudflare Access Login → Dashboard rendert → KPI-Tile zeigt `Cash USD ≈ 16 715,74` (oder neuerer Stand).

Telegram-Bot: `/status` → keine `unknown`-Felder. KAI-Widget reagiert.

---

## Phase 4 — Watchdog-Push-Verify (5-10 Min)

> **Risiko-Hinweis (V25-D-Status):** Während die Watchdog-Probe läuft, ist der Telegram-Listener **wirklich offline**. Memory `kai_listener_resilience_v25d_status.md` zeigt: F1+F6 done, F2 (log+raise+systemd) noch offen. Wenn parallel ein FloodWait/AuthKey-Issue zieht, gibt das eine echte Approval-Lücke. Mitigation:
>
> 1. Operator-Telegram **vor** der Probe: "Approval-Pause T+0 bis T+8 min — Watchdog-Test."
> 2. Probe-Fenster auf **mindestens 7 min** auslegen. Der Watchdog-Timer hat **5-Minuten-Cadenz** (Pi-4b-Beleg: journalctl zeigt Ticks 09:21 → 09:26 → 09:31 → 09:36 → 09:41 → 09:46 → 09:51 → 09:56 — exakt 5 min). Bei kürzerer Wartezeit fängst du zwischen zwei Ticks ein `inactive` ab, obwohl die Mechanik gleich greifen würde — false negative. Pi-4b-Probe 2026-05-05 09:53/09:54 UTC mit 130s/68s war exakt dieser Fall.
> 3. Probe **nach** der 24h-Stabilität wiederholen, nicht direkt im Cutover-Fenster — dann ist das Risiko klein.
> 4. Wenn die Probe scheitert, sofort manuell `systemctl start kai-tg-listener` und Watchdog separat debuggen, nicht bis zum nächsten Cycle warten.

```bash
# Operator vorab informieren — Approval-Pause für 8 min:
# (manuelle Aktion: Telegram-Push abschicken)

# Provoziere Service-Death zur Watchdog-Probe:
ssh $PI5 'sudo systemctl stop kai-tg-listener'
date -u +"stopped at: %Y-%m-%dT%H:%M:%SZ"

# Watchdog-Timer-Cadenz = 5 min, also 7 min Puffer (mind. ein Tick + Toleranz):
sleep 420
date -u +"checked at: %Y-%m-%dT%H:%M:%SZ"
ssh $PI5 'systemctl is-active kai-tg-listener'
# erwartet: active
```

**Ergebnis-Definition (Probe gilt nur als grün, wenn der Watchdog der Auslöser war):**

```bash
# journalctl-Beweis abrufen — der Watchdog muss den restart explizit geloggt haben:
ssh $PI5 'journalctl -u kai-service-watchdog.service --since "10 min ago" --no-pager | \
          grep -E "alarm|start_ok|kai-tg-listener=inactive"'
# Erwartet (zwei Zeilen):
#   "KAI service-watchdog: 1 alarm(s) @ ..."
#   "- [svc] kai-tg-listener=inactive; restart=start_ok:active"
#
# Wenn keine alarm-Zeile auftaucht, der Listener aber active ist:
# -> systemd hat ihn evtl. via Restart-Policy in der Unit-Datei selbst restartet,
#    NICHT der externe Watchdog. Das ist eine andere Mechanik und beweist NICHT,
#    dass kai-service-watchdog seinen Job tut.
```

```bash
# Wenn nach 420 s noch "inactive": sofort manuell starten + Watchdog journalctl prüfen:
ssh $PI5 'sudo systemctl start kai-tg-listener && \
          journalctl -u kai-service-watchdog.service --no-pager -n 30'
```

Plus Telegram-Push an Operator nach erfolgreichem Auto-Restart (siehe Memory `project_pi_migration_progress.md` § Watchdog).

> **Alternative — Watchdog-Probe nach T+24h verschieben:** wenn das Risiko zur Cutover-Zeit zu hoch ist (z. B. Cutover läuft am Wochenende, kein zweiter Operator als Backup), kann diese Phase auch nach 24 h stabilem Betrieb separat gefahren werden. Das ist konservativ und korrekt; nur muss die Verzögerung im Cutover-Postmortem dokumentiert werden, damit sie nicht vergessen wird.

---

## Phase 5 — Pi 4b Cold-Standby (24 h)

- Pi 4b bleibt 24 h **stromversorgt aber Services-stopped** als Rollback-Reserve.
- Falls Pi 5 zwischen T+0 und T+24h destabilisiert → Reverse-Cutover (Phase 2 in umgekehrter Richtung).
- Nach 24 h Pi-5-Stabilität: Pi 4b shutdown, dev.db archivieren als `data/dev_db_pi4b_final_<date>.snapshot`.

---

## Failure-Branches

### F1 — `AuthKeyDuplicatedError`-Storm beim Pi-5-tg-listener-Start

**Symptom:** Pi 5 tg-listener crasht sofort mit AuthKeyDuplicated, log-Spam.

**Ursache:** Phase 2.3 Logout vergessen ODER 5-Min-Wartezeit zu kurz.

**Recovery:**
1. Pi 5 tg-listener stoppen.
2. `data/telegram_session.session` am Pi 5 löschen.
3. Auf Pi 5 manuell neu authentifizieren: `python -c "from app.ingestion.telegram_channel_worker import authenticate; authenticate()"` (gibt Telegram-Code-Prompt — Operator interaktiv).
4. tg-listener neu starten.

### F2 — cloudflared-Tunnel routet nicht zu Pi 5

**Symptom:** `kai-trader.org/dashboard/` antwortet 502 oder timeout.

**Ursache:** Pi 5 cloudflared-Service läuft nicht, oder credentials.json invalid.

**Recovery:**
1. `journalctl -u cloudflared --no-pager -n 50`.
2. Credentials-File-Permissions prüfen: `chmod 600 ~/.cloudflared/*.json`.
3. Falls 24h-Reverse-Cutover-Zeit: Pi 4b cloudflared wieder hoch + Pi 5 zurückfahren.

### F3 — SQLite `dev.db` korrumpiert beim scp

**Symptom:** Pi 5 kai-server crasht mit SQLite-Errors.

**Ursache:** Pi 4b kai-server war nicht sauber gestoppt vor scp (WAL-File offen).

**Recovery:**
1. Pi 4b kai-server explizit stoppen + 5 Sek warten.
2. `cd /home/ubuntu/ai_analyst_trading_bot && sqlite3 data/dev.db "PRAGMA wal_checkpoint(TRUNCATE);"` am Pi 4b.
3. Re-scp dev.db nach Pi 5.

### F4 — OOM-Kill-Pattern auch am Pi 5

**Erwartung:** sehr unwahrscheinlich (16 GB RAM vs. 894 MB Pi 4b), aber Memory-Lehre: nicht als gegeben annehmen.

**Recovery:** `dmesg | grep -i kill` + `journalctl -k --since '1 hour ago'`. Pi 5 hat kein Swap initial — bei Bedarf 4 GB Swap-File anlegen (`fallocate -l 4G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile && echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab`).

### F5 — Telegram-FloodWait-Pattern persistiert nach Cutover

**Symptom:** Auch Pi 5 tg-listener loggt 1000+ `InvalidBufferError 429` (siehe V19-Diagnose 2026-05-04).

**Ursache:** Pattern ist Listener-Code-bedingt, nicht Hardware-bedingt → Cutover migriert nur das Problem.

**Recovery:** Listener-Reactivity-Fix (V20-Folge) MUSS vor Pi-5-Cutover ODER unmittelbar danach passieren. Andernfalls droht die 4-Tage-Stille auch auf Pi 5.

---

## Rollback-Pfad (T+0 bis T+24h)

1. Pi 5 alle Services stoppen.
2. Pi 5 cloudflared stoppen.
3. Pi 4b cloudflared starten (Tunnel routet wieder zu Pi 4b).
4. Pi 4b alle Services starten (umgekehrte Phase-3.1-Sequenz).
5. Operator-Telegram: "Rollback abgeschlossen, Pi 4b ist wieder Source-of-Truth".
6. Forensik **post-mortem**: was hat den Cutover gebrochen? Memo schreiben unter `artifacts/runbooks/pi5_cutover_postmortem_<date>.md`.

---

## Post-Cutover Memory-Pflege

Nach erfolgreichem T+24h-Verify:

- `project_pi_migration_progress.md` — Hardware-Section auf Pi 5 16 GB updaten.
- `project_pi5_migration.md` — Status auf "completed" setzen, Datum + Commit-Hash + Pi-5-IP eintragen.
- `feedback_maintenance_restart_protocol.md` — bleibt scharf (gilt unverändert für Pi 5).
- Daily Strategy 2026-05-0X — V17 als ✅ done mit Cutover-Fenster + Findings markieren.

---

## Quick-Reference SSH-Commands (Operator-Copy-Paste)

```bash
# Pi 4b Status:
ssh ubuntu@192.168.178.20 'for s in kai-server kai-agent-worker kai-tg-listener cloudflared kai-service-watchdog.timer; do printf "%-32s %s\n" $s $(systemctl is-active $s); done'

# Pi 5 Status (nach Setup):
ssh ubuntu@<pi5-ip> 'for s in kai-server kai-agent-worker kai-tg-listener cloudflared kai-service-watchdog.timer; do printf "%-32s %s\n" $s $(systemctl is-active $s); done'

# Audit-Hash beidseitig:
ssh ubuntu@192.168.178.20 'cd /home/ubuntu/ai_analyst_trading_bot && wc -l artifacts/paper_execution_audit.jsonl && sha256sum artifacts/paper_execution_audit.jsonl'
ssh ubuntu@<pi5-ip>       'cd /home/ubuntu/ai_analyst_trading_bot && wc -l artifacts/paper_execution_audit.jsonl && sha256sum artifacts/paper_execution_audit.jsonl'

# Tunnel-Smoke:
curl -I https://kai-trader.org/dashboard/
```

---

**Final Check:** wenn alle 6 Services active + Audit-Hash identisch + cloudflared routet + Watchdog-Push-Probe erfolgreich + Operator-Browser-Smoke OK → Cutover ✅ **DONE**.
