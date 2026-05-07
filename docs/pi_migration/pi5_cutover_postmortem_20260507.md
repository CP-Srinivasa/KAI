# Pi 5 Cutover Postmortem — 2026-05-07

**Ausführungs-Fenster:** 2026-05-07 07:55 — 08:20 UTC (~25 min Critical Section, ~85 min ab Pre-Flight)
**Ergebnis:** ✅ DONE. Pi 5 ist Source-of-Truth, Pi 4b ist 24h-Cold-Standby bis 2026-05-08.
**Operator:** Sascha
**Runbook:** `artifacts/runbooks/pi5_cutover.md` (HEAD `b46ed4a`)

---

## Bilanz

| Phase | Soll | Ist |
|---|---|---|
| 0+1 Pre-Cutover (Vortag) | 2026-05-06 ~17:20 UTC | ✅ |
| 2.1 Operator-Approval-Pause-Push | manuell | ✅ |
| 2.2 Pi 4b Services stoppen | 5 Services | ✅ + cloudflared automatisch via `Requires=` |
| 2.3 Telegram-Logout (Variante A) | `scripts/telegram_logout.py` | ✅ exit=0, "logged out" |
| 2.4 WAL-Checkpoint + scp + Hash-Verify | 12 Files | ✅ aber **11 Files** (Session-File durch `log_out()` weg) |
| 2.5 systemd enable | manuell ohne `--now` | ✅ |
| 2.6 Settings-Validator-Smoke | `OK env=…` | ✅ `OK env=development bind=127.0.0.1 …` |
| **2.7 Re-Auth (improvisiert)** | nicht im Runbook | ✅ Operator interaktiv durch `setup_auth()` |
| 3.1 Service-Start sequenziell | 4 Services | ✅ aber kai-server **Erststart-Failure** (logs/-Dir fehlt) |
| 3.2 6-Service-Smoke | alle active | ✅ |
| 3.3 TCP-Verify Telegram | ESTABLISHED 149.154.x | ✅ 2× ESTAB (agent-worker pid 11182, tg-listener pid 11430) |
| 3.4 Audit-Continuity | Hash-Match Pi 4b | ✅ 86 Z., sha256 `efb425a5…` identisch |
| 3.5 Tunnel-Switch | Pi 4b stop → Pi 5 start | ✅ effektiv schon durch 2.2-Side-Effect; Pi 5 cloudflared `active`, Pi 4b `inactive` |
| 3.6 Operator-Browser+/status | manuell | ✅ |
| 4 Watchdog-Probe | T+24h verschoben | ⏸ pending 2026-05-08 |
| 5 Pi 4b Cold-Standby | 24h passiv | ⏸ pending bis 2026-05-08 |

**Tunnel-Downtime gemessen:** ~25 min (07:55:16 → ~08:20 UTC, durch `Requires=kai-server` ab Phase 2.2 statt erst 3.5).
**Audit-Anker ungebrochen:** sha256 `efb425a58450b43811bfbba4eb5caf942058bfc0fa1fa9005b8ae8118134ac9f` (Pi 4b == Pi 5).
**F6 Listener-Reactivity:** `.telegram_channel_replay.json: scanned=0, processed=0, skipped_no_checkpoint=0` → Bug nicht aktiv.
**Pi 4b Ende-Audit-Hashes:** Snapshot `pi4b_snapshot_20260507T0743Z.txt` (gitignored, lokal).

---

## Befunde — 3 Runbook-Lücken

### B-1 — `telethon.log_out()` zerstört die lokale `.session`-Datei

**Symptom:** Phase 2.4.1 Hash-Snapshot crasht weil `artifacts/telegram_channel.session` nicht existiert. `find` findet auch repo-weit nichts.

**Ursache:** `client.log_out()` (Default-Verhalten in telethon 1.43.x): server-side AuthKey invalidieren UND lokale Session-Datei löschen. Das passiert in Phase 2.3 — bevor Phase 2.4 sie kopieren würde.

**Operator-Impact im Cutover:** Das Konzept „Session-File von alt nach neu" ist auch ohne den Bug logisch tot — ein server-side invalidierter AuthKey + clientseitige Session = `AuthKeyUnregisteredError` beim ersten Connect. Die einzige korrekte Sequenz nach `log_out()` ist **interaktive Re-Auth auf dem Ziel-Host**.

**Fix-Pfad:** Runbook-Phase 2.4 Pflicht-File-Liste ohne Session-File, neue Phase 2.7 als feste Phase: `python -c "import asyncio; from app.ingestion.telegram_channel_worker import setup_auth; asyncio.run(setup_auth())"` mit Operator-Aktion (Phone + Code + 2FA).

### B-2 — `cloudflared.service` zieht via `Requires=kai-server.service` automatisch mit

**Symptom:** Nach `systemctl stop kai-server kai-agent-worker kai-tg-listener kai-paper-trading.timer kai-service-watchdog.timer` (ohne cloudflared) ist der Tunnel-Connector trotzdem `inactive`. kai-trader.org antwortet ab Phase 2.2 nicht mehr.

**Ursache:** `deploy/systemd/cloudflared.service` Z.7: `Requires=kai-server.service`. systemd-Semantik: stop-propagation runs in beide Richtungen — wenn die required-unit stoppt, stoppt auch die requirer-unit. Die Runbook-Annahme „cloudflared bleibt während Phase 2.2 active" stimmt nicht.

**Operator-Impact im Cutover:** Tunnel-Downtime startet ~25 min früher als geplant (Phase 2.2 statt 3.5). Operator-Push (Phase 2.1) hat das mit „30–40 Min" gedeckt — kein Schaden, aber bei knapperem Push-Fenster (z.B. 15 min) wäre das gerissen.

**Fix-Pfad:** Runbook Phase 2.1 Push-Vorschlag-Text auf realistische Tunnel-Downtime kalibrieren (≥ 30 min). Phase 2.2 Hinweis ergänzen: cloudflared geht durch Requires automatisch mit `inactive`. Phase 3.5 entsprechend verkürzen (nur Pi 5 cloudflared start; Pi 4b ist schon down).

### B-3 — `kai-server` Erststart auf Blank-Slate-Host crasht weil `logs/` fehlt

**Symptom:** `sudo systemctl start kai-server` failt sofort mit:
```
kai-server.service: Failed to set up standard output: No such file or directory
control process exited, code=exited, status=209/STDOUT
```
Active-State: `activating (auto-restart)` (Restart-Policy + Failure-Loop bis logs/ existiert).

**Ursache:** Henne-Ei. Unit-File hat sowohl
```
StandardOutput=append:/home/kai/ai_analyst_trading_bot/logs/server.log
ExecStartPre=+/bin/bash -c 'mkdir -p .../logs && chown -R ubuntu:ubuntu .../logs'
```
systemd öffnet `StandardOutput=append:` **vor** `ExecStartPre`. Wenn `logs/` nicht existiert, schlägt der Open fehl bevor das mkdir laufen kann. Das gilt für `kai-server`, `kai-agent-worker`, `kai-tg-listener`, `cloudflared` — alle 4 schreiben in `logs/`.

**Operator-Impact im Cutover:** Pi 4b war historisch davon nicht betroffen, weil `logs/` aus früheren Runs schon existierte. Pi 5 als Blank-Slate hat das Verzeichnis nicht — Erststart blockiert. Workaround im Cutover: manuelles `mkdir -p /home/kai/ai_analyst_trading_bot/logs && chown -R ubuntu:ubuntu …/logs`.

**Fix-Pfad (im Repo):** `deploy/tmpfiles/kai.conf` mit `d /home/kai/ai_analyst_trading_bot/logs 0755 ubuntu ubuntu` einchecken; `scripts/pi_install_systemd.sh` extend um Tmpfiles-Install + `systemd-tmpfiles --create kai.conf`. Damit ist `logs/` vor jedem Service-Start durch `systemd-tmpfiles-setup.service` garantiert vorhanden, ExecStartPre kann als Defense-in-Depth (chown bei Permission-Drift) bleiben.

---

## Action-Items

| ID | Aktion | Status |
|---|---|---|
| A-1 | Runbook patchen (B-1 Phase 2.4 + 2.7, B-2 Phase 2.2 + 3.5, B-3 Phase 1.3 oder 2.5) | siehe Folge-Commit |
| A-2 | Repo-Fix B-3 — `deploy/tmpfiles/kai.conf` + `pi_install_systemd.sh` extend | siehe Folge-Commit |
| A-3 | Watchdog-Probe (Runbook Phase 4) auf Pi 5 fahren | T+24h, 2026-05-08 |
| A-4 | Pi 4b shutdown + `data/dev_db_pi4b_final_20260508.snapshot` archivieren | T+24h, 2026-05-08 |
| A-5 | Memory-Pflege: `kai_pi5_cutover_track.md` → completed, F-Status-Memory → Pi 5 live | ✅ |

---

## Cross-Refs

- Runbook: `artifacts/runbooks/pi5_cutover.md`
- Tag (Rollback-Anker): `pre-pi5-cutover-2026-05-05` (`210f526`)
- HEAD beim Cutover: `b46ed4a` (chore(pi5-cutover): pre-cutover hygiene — telegram-logout + logrotate)
- Memory: `feedback_pi_cutover_runbook_gaps.md` (3 Lehren detailliert für nächsten Cutover/DR)
- Decision-Refs: D-216 (Cutover-Anker), D-220 (Pi 4b Sync-Stand), D-208 (logs/-Permission-Vorhistorie)
