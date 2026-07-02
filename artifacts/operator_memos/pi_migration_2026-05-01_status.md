# Pi-Migration — D-5-Statusmemo (2026-04-26)

**Cutover-Datum:** 2026-05-01 (in 5 Tagen)
**Pre-Flight-Doku:** `docs/pi_migration/preflight.md` (V3-verifiziert 2026-04-22)
**Operator vor Ort:** ja, ab 2026-05-01

Dieses Memo ist die D-5-Bestandsaufnahme: **was ist fertig, was ist offen, wo entstanden seit 04-22 neue Befunde**.

---

## 1. Stand der Vorbereitungs-Artefakte

| Artefakt | Status | Verweis |
|---|---|---|
| Pre-Flight-Doku | ✅ vollständig | `docs/pi_migration/preflight.md` (358 Zeilen, 11 Sektionen) |
| systemd-Unit-Files | ✅ vorhanden, 9 Units | `deploy/systemd/*.service`, `*.timer` |
| `pi_install_systemd.sh` | ✅ idempotent, Dry-Run-fähig | `scripts/pi_install_systemd.sh` |
| `pi_transfer_artifacts.sh` | ⚠️ **DB-Lücke**, sonst OK | `scripts/pi_transfer_artifacts.sh` |
| `paper_trading_cron.sh` | ✅ Bash-Port mit funktionaler Parität | `scripts/paper_trading_cron.sh` |
| Backup-Skript | ✅ live | `scripts/kai_backup_artifacts.sh` (vor D-190 lokal vorhanden, formell committed via D-207) |

---

## 2a. KRITISCHER BEFUND — rsync fehlt auf Laptop

**Heute entdeckt 2026-04-26.** `which rsync` → not found. Git-Bash/MSYS2 auf diesem Laptop liefert kein rsync. `pi_transfer_artifacts.sh` ruft aber `rsync -avz --checksum --partial --human-readable --mkpath` — der Cutover-Transfer würde hart abbrechen.

`scp` ist verfügbar (OpenSSH).

### Optionen (Entscheidung vor 05-01 nötig)

| Option | Aufwand | Robustheit | Kommentar |
|---|---|---|---|
| **B1 — Tarball-Bundle + scp** | klein | hoch | `tar -czf bundle.tar.gz <paths> && scp bundle.tar.gz pi: && ssh pi 'tar -xzf …'`. Deterministisch, kein Resume bei Abbruch. |
| **B2 — rsync via cwRsync/MSYS2 nachinstallieren** | mittel | hoch | Eingriff in Laptop-Setup. cwRsync 6.4 kostenlos. |
| **B3 — Pi-Pull statt Laptop-Push** | mittel | mittel | Pi pulls per ssh+rsync vom Laptop → Laptop muss sshd haben (hat er nicht). Verworfen. |
| **B4 — Skript auf scp+sha256 umschreiben** | mittel | hoch | Größerer Patch in `pi_transfer_artifacts.sh`. Fall-through für Operator: nur 1 Skript am Pi-Tag. |

**Empfehlung: B4** (Skript auf scp+tar umstellen) — saubere Operator-UX am 05-01, ein Skript für alle Gruppen, idempotente Verify-Prüfung via sha256.
**Aufwand B4:** 60-90 min Implementation + 15 min Test gegen localhost. **P0**, aber nicht heute zwingend — kann morgen.

---

## 2b. KRITISCHER BEFUND — SQLite-DB nicht im Transfer-Skript

**Heute entdeckt 2026-04-26.**

`DB_URL=sqlite+aiosqlite:///data/dev.db` → die produktive DB ist eine SQLite-Datei `data/dev.db` (**20 MB**, frisch geschrieben). `scripts/pi_transfer_artifacts.sh` deckt aber nur `artifacts/` ab — `data/` fehlt komplett.

**Konsequenz wenn unverändert:** Pi startet nach Migration mit leerer DB → Verlust der gesamten persistenten Domain-Daten (Source-Registry, Document-Tabellen, Decisions, Position-State je nach Schema-Verteilung). JSONL-Audit-Trails wären auf dem Pi, aber die Repositories darauf zeigen ins Leere.

**Damit ist §11.3 (Postgres-pg_dump-Strategie) obsolet** — wir sind nie auf Postgres umgestiegen. Stattdessen:

### Fix vor Cutover (vorgeschlagen)
1. **`pi_transfer_artifacts.sh` erweitern**: neue Gruppe `database` mit `data/dev.db`. Pflicht-Default (nicht opt-in), weil Re-Entry-Datenbasis sonst weg ist.
2. **Cutover-Schritt einfügen** (preflight.md §5 zwischen Punkt 1 und 2): „Vor Laptop-Stop: letzten DB-Snapshot vom Laptop auf Pi syncen — sonst gehen die letzten Minuten verloren." Idealerweise in Stop-Skript verdrahten: `server_stop.sh` löst final-rsync der DB aus, *bevor* es den Server killt.
3. **Verifikation**: `sha256sum data/dev.db` beidseitig vergleichen, Pi-Schema mit `python -m alembic current` auf gleicher Revision wie Laptop prüfen.

**Aufwand:** 30-45 min (Skript erweitern + 1 Test-Sync). **P0-Blocker** — ohne diesen Fix kein Cutover.

---

## 3. Status der offenen Punkte aus preflight.md §8

| # | Punkt | Stand 2026-04-26 | Action |
|---|---|---|---|
| 1 | CLI-Entry `paper-trading cron` | ✅ erledigt 04-20 | — |
| 2 | Agent-Worker-Entrypoint | ✅ erledigt 04-20 | — |
| 3 | `daily-strategy bootstrap` ohne TTY | ✅ erledigt 04-22 | — |
| **4** | **Port 8000 frei auf Pi** | ❓ ungeprüft | Operator: am 05-01 vor `kai-server.service` enable: `ss -tlnp \| grep ':8000 '` — wenn belegt: was läuft da? |
| **5** | **Zeitzone Pi → Europe/Berlin** | ❓ ungeprüft | Operator: am 05-01 nach OS-Check: `timedatectl set-timezone Europe/Berlin` (Standard-RaspiOS Bookworm ist UTC) |
| **6** | **SSH-Zugriff ab 05-02** | ❓ offen | **Empfehlung Cloudflare-Tunnel-SSH** (siehe §4 unten) — heute klären, am 05-01 setup. Alternative LAN-only ist nicht 24/7-tauglich für Remote-Debug. |
| **7** | **BotFather-Mini-App-URL** | ❓ offen | **Operator-Action heute** (5 min): @BotFather → /mybots → KAI-Bot → Bot Settings → Menu Button → URL. Aktuellen Wert melden. Wenn ≠ `https://kai-trader.org/dashboard/`: jetzt umstellen, dann ist der Reminder geschlossen. |

---

## 4. SSH-Zugriff via Cloudflare-Tunnel (§8.6 — Empfehlung)

Begründung: gleiche Tunnel-Infra wie der Web-Stack, keine offenen Ports im Heimrouter, 24/7 verfügbar.

**Setup auf dem Pi (05-01 nach Pi-Setup):**
```bash
# Pi: Tunnel-Config erweitern um SSH-Ingress
# ~/.cloudflared/config.yml:
#   ingress:
#     - hostname: kai-trader.org
#       service: http://127.0.0.1:8000
#     - hostname: ssh.kai-trader.org
#       service: ssh://localhost:22
#     - service: http_status:404
sudo systemctl restart cloudflared
```

**Cloudflare-DNS:** CNAME `ssh.kai-trader.org` → Tunnel-UUID (`beafc2ce-3c02-40c6-a6d7-359b2cc40cf6`).

**Client-Setup (Laptop, einmalig):**
```bash
# ~/.ssh/config
Host pi.kai-trader.org
  ProxyCommand cloudflared access ssh --hostname ssh.kai-trader.org
  User kai
```

Danach `ssh pi.kai-trader.org` von überall, ohne Router-Konfig.

**Aufwand:** 15-20 min am 05-01 nach Tunnel-Bring-up. **P1**, nicht Cutover-blockierend, aber innerhalb 24 h fertigstellen.

---

## 5. Cutover-Reihenfolge (verfeinert mit DB-Fix)

Reihenfolge für 2026-05-01 vor Ort, mit den heute identifizierten Lücken:

```
Tag 0 (heute, 04-26)
└─ §2.3 DB-Transfer-Skript-Patch          (Hauptagent, 30-45 min)
└─ §3.7 BotFather-URL prüfen/ändern        (Operator, 5 min)
└─ §4   SSH-Tunnel-Config-Plan validieren   (Operator, 5 min Lese-Bestätigung)

Tag D-3 (04-28, nächster Pre-Flight-Review)
└─ Re-Check §3.4 §3.5 §3.6                  (vor-Ort-Bedingungen)

Tag 0+5 (05-01)
├─ 09:00  Hardware Pi-Setup, OS, Python 3.12, User kai           (45 min)
├─ 09:45  Repo clone, venv, pip install -e .                     (45 min)
├─ 10:30  scp .env, ~/.cloudflared bundle, data/dev.db, artifacts/ (60 min, mit Verify)
├─ 11:30  systemd-Units installieren (pi_install_systemd.sh)     (15 min)
├─ 11:45  Lokale Health-Checks (curl 127.0.0.1:8000, /status)    (15 min)
├─ 12:00  Mittagspause                                            (60 min)
├─ 13:00  Cutover §5 — Laptop-Stop, Pi-Tunnel allein              (15 min)
├─ 13:15  Externe Validierung §7 (10 Haken)                      (30 min)
├─ 13:45  SSH-Tunnel-Config + Test                               (20 min)
└─ 14:00  Beobachtungsfenster startet — 48 h, Laptop bleibt OFFLINE-aber-NICHT-LÖSCHEN
```

Nettoaufwand 05-01: ~5 h aktive Arbeit + 48 h passive Beobachtung. Deckt sich mit §9-Schätzung.

---

## 6. Was JETZT (04-26) zu tun ist

Reihenfolge nach Priorität:

| # | Action | Wer | Aufwand |
|---|---|---|---|
| **A1** | DB-Transfer-Patch in `pi_transfer_artifacts.sh` (neue Gruppe `database`, Default-on) + Test-Sync gegen `localhost:/tmp` als Probe | Hauptagent | 30-45 min |
| **A2** | BotFather-URL prüfen/umstellen | **Operator** | 5 min |
| **A3** | DB-Final-Sync-Schritt in `server_stop.sh` einbauen (rsync data/dev.db an Pi-Host vor Server-Kill, opt-in via `--prepare-cutover`) | Hauptagent | 45 min |
| **A4** | SSH-Tunnel-Config-Plan im preflight.md §8.6 ergänzen (Block aus §4 dieses Memos) | Hauptagent | 10 min |
| A5 | Backup-Skript-Trockentest auf Pi-Empfang vorbereiten (target-Pfad existiert?) | Operator vor Ort | nicht heute |

Heute realistisch leistbar: A1 + A4 (Hauptagent, ~1 h zusammen) + A2 (Operator, 5 min). A3 kann morgen.

---

## 7. Verbleibende Risiken (ehrlich)

| Risiko | Wahrscheinlichkeit | Mitigation |
|---|---|---|
| ARM64-Wheel-Bauzeit (pyproject hat keine pinned Python 3.12) | niedrig (§11.2 grün) | Falls Compile-Zeit: 30-min-Buffer im Plan |
| `data/dev.db` lock-konflikt während Final-Sync | mittel | Cutover-Schritt **erzwingt** Server-Stop bevor rsync beginnt |
| Cloudflare-Tunnel doppelt aktiv (Laptop + Pi gleichzeitig) während §5 | mittel | Schritt 1 (Pi-Stack starten) bewusst VOR Schritt 2 (Laptop-Stop) — 60 s Overlap akzeptabel |
| BotFather-URL doch noch nicht geändert | mittel (heute klären!) | A2 |
| Pi-Stromausfall / Netzwerk-Hiccup während ersten 48 h | niedrig (Pi 4b stabil) | Rollback §10 in <2 min möglich, Laptop bleibt 48 h dran |

---

## 8. Querverweise

- `docs/pi_migration/preflight.md` — Master-Doku
- `reminder_server_migration_pi.md` — Memory-Reminder
- `reminder_cloudflare_named_tunnel.md` — Tunnel-Status (BotFather-URL offen)
- `security_e1_key_rotation.md` — rotierte Tokens sind in `.env` enthalten
- `artifacts/operator_memos/backup_strategy_2026-04-25.md` — Backup-Strategie

---

## 9. D-3 Re-Check (2026-04-28)

Stand der A-Items aus §6 (Plan vom 04-26), validiert am 04-28:

| ID | Action | Stand 2026-04-28 | Verifikation |
|---|---|---|---|
| **A1** | DB-Gruppe in `pi_transfer_artifacts.sh` (Default-on) | ✅ erledigt | Commit `f285370` (D-200). `grep "data/dev.db\|database" scripts/pi_transfer_artifacts.sh` → 5 Treffer; `database` ist in `SELECTED_GROUPS` Default. |
| **A2** | BotFather-URL prüfen/umstellen | ❓ **Operator-Status unbekannt** | **Bitte Operator melden** ob auf `https://kai-trader.org/dashboard/` umgestellt. Wenn nicht: 5 min Action am Pi-Tag früh. |
| **A3** | DB-Final-Sync in `server_stop.sh --prepare-cutover` | ✅ **erledigt 2026-04-28** (D-206, C1-Pfad) | `grep prepare-cutover scripts/server_stop.sh` → mehrere Treffer; 9 Bash-Integration-Smokes (`tests/integration/test_server_stop_cutover_bash.py`) grün. Siehe §9.1 unten. |
| **A4** | preflight.md §8.6 SSH-Tunnel-Block | ✅ erledigt | `docs/pi_migration/preflight.md:275–303` enthält Tunnel-Config + Cloudflare-Application-Policy + Client-Config. |
| A5 | Backup-Trockentest | ⏳ Operator vor Ort am 05-01 | nicht heute |

**Zusätzlich seit 04-26 erledigt:**
- D-201 `/status` Cycle-Histogramm + WARP-Indikator + Replay-Felder (deckt 04-26-Carry-overs M2/M3/M5)
- D-202 `re_entry_mode` Boot-Fail-Invariants (verhindert dass Pi-Stack mit aktivem Re-Entry-Mode startet, falls Migrations-Drift)

### 9.1 A3 — C1-Pfad implementiert (D-206, 2026-04-28)

**Status: ✅ geschlossen.** `scripts/server_stop.sh --prepare-cutover=<ssh-host>` orchestriert Pre-Flight → Server-Stop → DB-Sync → sha256-Verify in einem Befehl.

**Implementation:**
- Pre-Flight (LÄUFT VOR dem Stop): `scp/ssh/sha256sum/awk` auf PATH? `data/dev.db` da? SSH-Probe (`ConnectTimeout=10s`, `BatchMode=yes`) erfolgreich? Bei Fehler: Server bleibt oben, Exit 2.
- Server-Stop: identische Logik wie der unflagge Pfad. Wenn Stop scheitert (z.B. Access-Denied), wird Cutover-Sync **nicht** ausgeführt — DB könnte mid-write sein.
- DB-Sync: `ssh mkdir -p`, `scp data/dev.db`, `ssh sha256sum`, lokal-vs-remote Vergleich. Single-File-Operation via scp, **kein** rsync nötig — funktioniert auf diesem Laptop trotz fehlendem rsync (B4 bleibt für Bulk-Transfer relevant, blockiert C1 aber nicht).
- Default `--remote-root=/home/kai/ai_analyst_trading_bot`, override via Flag.

**Tests:** 9 Bash-Integration-Smokes in `tests/integration/test_server_stop_cutover_bash.py`:
- syntax check (`bash -n`)
- baseline ohne Flag unverändert (Regression-Schutz)
- `--prepare-cutover` ohne Wert → Exit 2
- unbekannte Flags → Exit 2
- fehlende `data/dev.db` → Exit 2 vor Server-Stop
- ssh-Probe-Fail → Exit 2 vor Server-Stop
- Happy-Path mit gestubten ssh/scp/sha256sum → Exit 0
- sha256-Mismatch → Exit 2 mit Diagnose
- `--remote-root` override threadet sauber durch

**Operator-UX am Pi-Tag:** Schritt 3.3 in §10 wird einzeilig:
```
bash scripts/server_stop.sh --prepare-cutover=kai@pi.local
```
C2-Manual-Pfad bleibt im Memo dokumentiert als Fallback bei kaputter SSH-Auth o.Ä.

C3 (DB-Lücke akzeptieren) bleibt verworfen — eine Cutover-Lücke am Re-Entry-Datenstrom ist genau das, was wir nicht riskieren wollen.

### 9.2 §8.4–§8.7 — Pi-Tag-Aktionen (Operator-Hand)

| §  | Was | Wann | Wer | Befehl/Action |
|----|-----|------|-----|---------------|
| 8.4 | Port 8000 frei prüfen | direkt nach Pi-OS-Boot | Operator | `ss -tlnp \| grep ':8000 '` — wenn leer: weiter. Wenn belegt: `sudo systemctl status <unit>`, Service identifizieren, Entscheidung: stoppen oder KAI auf 8001 umkonfigurieren (`.env: API_PORT=8001`). |
| 8.5 | Zeitzone setzen | direkt nach §8.4 | Operator | `timedatectl set-timezone Europe/Berlin && timedatectl` (Verifikation: Local time entspricht Wanduhr) |
| 8.6 | SSH-Tunnel aktivieren | nach Stack-Up + Tunnel-Restart | Operator | preflight.md §8.6 Schritt 1-3: Tunnel-Config erweitern, `cloudflared restart`, CNAME `ssh.kai-trader.org` setzen, Cloudflare-Access-Application + Policy konfigurieren. Test: `ssh pi.kai-trader.org` vom Laptop. |
| 8.7 | BotFather-URL | **heute (04-28) erledigen, nicht erst am Pi-Tag** | Operator | @BotFather → /mybots → KAI-Bot → Bot Settings → Menu Button → URL=`https://kai-trader.org/dashboard/`. 5 min, schließt den Reminder. |

---

## 10. Konsolidierte Pi-Tag-Checkliste (2026-05-01)

**Eine einzige Liste zum Abhaken am Pi-Tag.** Reihenfolge ist verbindlich — Schritt N erst nach abgeschlossenem N-1.

### Phase 1 — Pi-OS bring-up (~45 min)
- [ ] **1.1** Pi 4b booten, RaspiOS Bookworm installiert (vorab erledigt: prüfen)
- [ ] **1.2** `ssh kai@pi.local` funktioniert (LAN, vor Cloudflare-SSH)
- [ ] **1.3** `sudo apt update && sudo apt upgrade -y && sudo apt install -y python3.12 python3.12-venv git rsync sqlite3 cloudflared`
- [ ] **1.4** `timedatectl set-timezone Europe/Berlin && timedatectl` → Local time stimmt (§8.5)
- [ ] **1.5** `ss -tlnp | grep ':8000 '` → leer (§8.4). Falls nicht: identifizieren + entscheiden (stop oder API_PORT=8001 in `.env`)

### Phase 2 — Repo + venv (~45 min)
- [ ] **2.1** `git clone https://github.com/<user>/ai_analyst_trading_bot.git ~/kai && cd ~/kai`
- [ ] **2.2** `python3.12 -m venv .venv && source .venv/bin/activate`
- [ ] **2.3** `pip install -e .` — bei ARM64-Wheel-Compile: 30 min Buffer einplanen
- [ ] **2.4** `python -c "import app; print(app.__file__)"` → Pfad korrekt

### Phase 3 — Daten-Transfer vom Laptop (~60 min, mit Verify)
- [ ] **3.1** Laptop: `git push` aktueller Stand (alle uncommitted Änderungen sichern!)
- [ ] **3.2** Laptop: `scripts/pi_transfer_artifacts.sh --target=pi.local --dry-run` → Diff-Liste reviewen
- [ ] **3.3 (DB-Final-Sync)** **C1-Pfad (Standard, A3 implementiert D-206):** Laptop: `bash scripts/server_stop.sh --prepare-cutover=kai@pi.local` → Pre-Flight + Server-Stop + scp + sha256-Verify in einem Befehl. Exit 0 = DB-Sync verifiziert. Exit 2 = Pre-Flight oder sha256-Mismatch (Memo §9.1 für Diagnose). **C2-Pfad (Fallback bei kaputter SSH-Auth):**
  - [ ] 3.3a Laptop: `bash scripts/server_stop.sh` (Server stoppt, DB ist write-quiescent)
  - [ ] 3.3b Laptop: `sha256sum data/dev.db > /tmp/db_pre.sha`
  - [ ] 3.3c Laptop: `scp data/dev.db kai@pi.local:~/kai/data/dev.db`
  - [ ] 3.3d Pi: `sha256sum ~/kai/data/dev.db` → muss `db_pre.sha` matchen
- [ ] **3.4** Laptop: `scripts/pi_transfer_artifacts.sh --target=pi.local` (alle anderen Gruppen außer `database`, das ist schon in 3.3 erfolgt — Skript erkennt das per sha256-Vergleich)
- [ ] **3.5** Pi: `ls -la ~/kai/artifacts/ ~/kai/data/dev.db` → Größen plausibel
- [ ] **3.6** `.env` und `~/.cloudflared/cert.pem`+`config.yml` separat per scp übertragen (nicht via Skript — Secrets!)
- [ ] **3.7** Pi: `python -m alembic current` → Revision matcht Laptop-Output

### Phase 4 — systemd + Health-Check (~30 min)
- [ ] **4.1** Pi: `bash scripts/pi_install_systemd.sh --dry-run` → Reviewen
- [ ] **4.2** Pi: `bash scripts/pi_install_systemd.sh` → 9 Units installiert
- [ ] **4.3** Pi: `sudo systemctl enable --now kai-server.service`
- [ ] **4.4** Pi: `curl -s 127.0.0.1:8000/healthz` → 200 OK
- [ ] **4.5** Pi: `curl -s 127.0.0.1:8000/status | jq '.envelope, .telegram_channel_ingest, .cycle_breakdown'` → keine `unknown`-Werte außer den dokumentierten Stubs
- [ ] **4.6** Pi: `sudo systemctl enable --now kai-paper-trading.timer kai-tg-listener.service kai-watchdog.service kai-backup.timer`

### Phase 5 — Cutover (Tunnel-Switch) (~15 min)
- [ ] **5.1** Pi: `sudo systemctl restart cloudflared` → neuer Tunnel-Endpoint live
- [ ] **5.2** Externes Gerät (Handy mit Mobilfunk, NICHT WARP): `https://kai-trader.org/dashboard/` lädt → wird **vom Pi** beantwortet
- [ ] **5.3** Laptop: `scripts/server_stop.sh` (war in Phase 3 schon erledigt, nur falls dazwischen wieder gestartet)
- [ ] **5.4** Laptop: `cloudflared` stoppen (`sudo systemctl stop cloudflared` oder Task-Manager) — **wichtig: nur eine Tunnel-Instanz aktiv**
- [ ] **5.5** Externes Gerät: erneut `https://kai-trader.org/dashboard/` → muss weiterhin funktionieren (jetzt eindeutig vom Pi)
- [ ] **5.6** Telegram: `/status` → Pi-Hostname / Uptime ist neu

### Phase 6 — SSH-Tunnel (~20 min, §8.6)
- [ ] **6.1** Pi: `~/.cloudflared/config.yml` um SSH-Ingress erweitern (preflight.md §8.6 Block)
- [ ] **6.2** Pi: `sudo systemctl restart cloudflared`
- [ ] **6.3** Cloudflare-Dashboard: CNAME `ssh.kai-trader.org` → `<tunnel-uuid>.cfargotunnel.com` (Proxy on)
- [ ] **6.4** Cloudflare-Access: Self-hosted Application für `ssh.kai-trader.org`, E-Mail-Allowlist
- [ ] **6.5** Laptop `~/.ssh/config` Block hinzufügen (preflight.md §8.6)
- [ ] **6.6** Laptop: `ssh pi.kai-trader.org` → erfolgreich

### Phase 7 — Externe Validierung (~30 min, preflight.md §7)
- [ ] **7.1** 10 Haken aus preflight.md §7 abarbeiten (Dashboard, Telegram-Bot, Webhook-Endpoint, /status etc.)
- [ ] **7.2** Letzten Re-Check: `curl https://kai-trader.org/status` von extern, alle Surfaces grün

### Phase 8 — Beobachtungsfenster (48 h passiv)
- [ ] **8.1** Laptop OFFLINE lassen, **NICHT LÖSCHEN** — Rollback-Option preflight.md §10 bleibt offen
- [ ] **8.2** Telegram-Watchdog-Pings beobachten
- [ ] **8.3** 2026-05-03 Mittag: Wenn keine Auffälligkeit → Laptop-KAI-Stack final deinstallieren (separater Schritt, kein Auto-Pilot)

**Aktive Pi-Tag-Arbeit:** ~4–5 h (Phase 1–7).
**Beobachtung:** 48 h passiv.
**Nicht delegierbar:** Phase 1, 3.3 (DB-Final-Sync), 5 (Cutover), 6 (SSH-Tunnel) — alle vor Ort am Pi.

---

## Status

- **Erstellt:** 2026-04-26 (D-5)
- **D-3 Re-Check:** 2026-04-28 ✅ (dieser Eintrag) — A1/A4 ✅, A3 ❌ (C2-Workaround dokumentiert), A2 Operator-bestätigung offen
- **Nächste Review:** 2026-04-30 (D-1) — wenn A3-Implementation oder Operator-A2 Status-Update
- **Eskalation auf P0:** A3-Lücke (DB-Final-Sync) — C2-Workaround eingeplant; C1-Implementierung empfohlen falls Slot heute noch frei.
