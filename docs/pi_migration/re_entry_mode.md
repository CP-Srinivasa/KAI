# Re-Entry-Mode: Boot-Fail-Aktivierung für die TV-Pivot-Quality-Bar

**Bezug:** D-202 (2026-04-26), `app/core/re_entry_mode.py`
**Re-Entry-Stichtag:** 2026-05-16
**Aktivierungstag:** 2026-05-15 (D-1 vor Re-Entry-Verdict)
**Default:** OFF — Laptop und Pi booten unverändert solange `RE_ENTRY_MODE_ENABLED=false`.

---

## 1. Was der Schalter macht

`re_entry_mode.enabled=true` aktiviert einen Settings-Validator, der beim Boot fail-loud crashed, wenn eine der folgenden Re-Entry-Invarianten nicht erfüllt ist. Alle Verstöße werden in **einer** Error-Message gesammelt — nicht first-fail, damit der Operator alle fehlenden Settings auf einen Schlag sieht.

| Invariante | Enforce-Flag (Default) | Settings-Quelle |
|---|---|---|
| S-001 Provenance-Secret nicht leer | `enforce_provenance_secret=true` | `alerts.provenance_secret` |
| S-002 Replay-Cache persistent | `enforce_replay_cache_persistent=true` | `tradingview.webhook_replay_cache_persistent` |
| S-002 Replay-Cache absoluter Pfad | `enforce_replay_cache_absolute_path=true` | `tradingview.webhook_replay_cache_db_path` |
| S-003 Watchdog-Heartbeat-Pfad gesetzt | `enforce_watchdog_heartbeat=true` | `telegram_channel_ingest.heartbeat_path` |
| B-002 Observability vollständig | `enforce_observability_complete=false` | Capability-Probe (heute hardcoded `False`) |

**Warum `enforce_observability_complete` Default `false` ist:** B-002 (LLM-Failure-Rate, Latency p95) ist zum Stand 2026-04-26 noch `not_implemented`. Ein True-Default würde jeden enabled-Boot scheitern lassen, bevor der Operator die Telemetrie nachgezogen hat. Bewusste Asymmetrie: dieser Sub-Switch ist Opt-In, alle anderen sind Opt-Out.

---

## 2. .env-Template für den Pi (am 2026-05-15 anwenden)

```bash
# Re-Entry-Mode Hardening (D-202)
RE_ENTRY_MODE_ENABLED=true
RE_ENTRY_MODE_ENFORCE_PROVENANCE_SECRET=true
RE_ENTRY_MODE_ENFORCE_REPLAY_CACHE_PERSISTENT=true
RE_ENTRY_MODE_ENFORCE_REPLAY_CACHE_ABSOLUTE_PATH=true
RE_ENTRY_MODE_ENFORCE_WATCHDOG_HEARTBEAT=true
RE_ENTRY_MODE_ENFORCE_OBSERVABILITY_COMPLETE=false  # auf true sobald B-002 implementiert ist

# S-001 — Provenance-Seal
ALERT_PROVENANCE_SECRET=<32-hex-chars-or-stronger>

# S-002 — Replay-Cache
TRADINGVIEW_WEBHOOK_REPLAY_CACHE_PERSISTENT=true
TRADINGVIEW_WEBHOOK_REPLAY_CACHE_DB_PATH=/var/lib/kai/tradingview_replay_cache.db

# S-003 — Watchdog-Heartbeat
INGESTION_TELEGRAM_CHANNEL_HEARTBEAT_PATH=/var/lib/kai/telegram_listener_heartbeat
INGESTION_TELEGRAM_CHANNEL_HEARTBEAT_STALE_SECONDS=1800
```

**Pfad-Konventionen Pi:**
- `/var/lib/kai/` ist der persistente State-Pfad (analog zu D-191 systemd-Unit-Konvention)
- Owner `kai:kai`, mode `0750`
- systemd-Unit braucht `ReadWritePaths=/var/lib/kai` damit Cache + Heartbeat schreibbar sind

---

## 3. Aktivierungs-Checkliste 2026-05-15

Auszuführen auf dem Pi unter `kai`-User, **bevor** `RE_ENTRY_MODE_ENABLED=true` gesetzt wird:

- [ ] **S-001 Secret generieren:** `python -c "import secrets; print(secrets.token_hex(32))"` — neuen Wert in `.env` als `ALERT_PROVENANCE_SECRET` setzen, **nicht** den Token aus `.env.backup.20260418_130027` recyclen.
- [ ] **S-002 Cache-Pfad anlegen:** `sudo install -d -o kai -g kai -m 750 /var/lib/kai`. Existierende Cache-DB aus `artifacts/` dorthin verschieben (nicht kopieren — verhindert Drift): `mv artifacts/tradingview_replay_cache.db /var/lib/kai/`.
- [ ] **S-003 Heartbeat-Pfad anlegen:** `touch /var/lib/kai/telegram_listener_heartbeat && chown kai:kai /var/lib/kai/telegram_listener_heartbeat`.
- [ ] **systemd-Units aktualisieren:** `kai-server.service` und `kai-agent-worker.service` brauchen `ReadWritePaths=/var/lib/kai`. `daemon-reload` + Restart.
- [ ] **Smoke vor Aktivierung:** `RE_ENTRY_MODE_ENABLED=false` lassen, einmal `python -c "from app.core.settings import get_settings; print(get_settings().re_entry_mode.enabled)"` — muss `False` zeigen ohne Crash.
- [ ] **Pre-Activation-Test:** in einem Throwaway-Shell-Env `RE_ENTRY_MODE_ENABLED=true` setzen + `python -c "from app.core.settings import AppSettings; AppSettings()"` ausführen. Erwartet: kein Crash. Wenn Crash: Error-Message zeigt welche Invariante fehlt — fixen, nochmal testen.
- [ ] **Activation:** `.env` mit `RE_ENTRY_MODE_ENABLED=true` speichern, `sudo systemctl restart kai-server kai-agent-worker`. `journalctl -u kai-server -n 50` prüfen.
- [ ] **Post-Activation-Verifikation:** (1) `/status`-Endpoint zeigt `re_entry_mode: enabled` (Feld muss noch im Status-Surface ergänzt werden — Follow-up); (2) `curl -X POST kai-trader.org/tradingview/webhook` mit Test-Payload → 200; (3) Heartbeat-Datei-mtime aktualisiert sich alle 60s (`stat /var/lib/kai/telegram_listener_heartbeat`).

---

## 4. Diagnose von Boot-Failures

Wenn `kai-server.service` nach Aktivierung nicht startet, liefert die Crash-Message alle Verstöße auf einmal:

```
ConfigurationError: re_entry_mode invariants violated:
  - ALERT_PROVENANCE_SECRET must be non-empty
  - TRADINGVIEW_WEBHOOK_REPLAY_CACHE_DB_PATH='artifacts/tradingview_replay_cache.db' is relative
```

Mapping Symptom → Fix:

| Error-Substring | Ursache | Fix |
|---|---|---|
| `provenance_secret is empty` | `ALERT_PROVENANCE_SECRET` fehlt oder leer | Token generieren, in `.env` setzen |
| `webhook_replay_cache_persistent must be true` | `TRADINGVIEW_WEBHOOK_REPLAY_CACHE_PERSISTENT` fehlt/falsch | Auf `true` setzen |
| `is relative` | `TRADINGVIEW_WEBHOOK_REPLAY_CACHE_DB_PATH` ist relativer Pfad | Auf absoluten Pfad ändern (`/var/lib/kai/...`) |
| `heartbeat_path is empty` | `INGESTION_TELEGRAM_CHANNEL_HEARTBEAT_PATH` explizit leergesetzt | Default wiederherstellen oder absoluten Pfad setzen |
| `B-002 not yet implemented` | `enforce_observability_complete=true` aber Capability noch False | Auf `false` zurücksetzen bis Telemetrie fertig |

---

## 5. Rollback

Bei Problemen, die nicht in 15 Minuten lösbar sind:

1. `.env` editieren: `RE_ENTRY_MODE_ENABLED=false`
2. `sudo systemctl restart kai-server kai-agent-worker`
3. Boot ist wieder graceful — alle anderen Settings (Provenance-Secret, Cache-Pfad, Heartbeat-Pfad) bleiben aktiv und schaden nicht.
4. Inzident in `DECISION_LOG.md` mit D-Nummer dokumentieren.

**Wichtig:** Rollback ist kein Re-Entry-Verlust. Daten werden weiter akkumuliert, nur ohne fail-loud-Garantie. Re-Entry-Verdict am 2026-05-16 kann mit oder ohne aktiven Schalter erfolgen — der Schalter ist Verifikation, nicht Voraussetzung.

---

## 6. Bekannte Lücken (Stand 2026-04-26)

- **`with_hash` fail-open in `app/signals/models.py:107-115`** — der Validator-Layer ist Single-Layer-Defense. Falls jemand zur Laufzeit `alerts.provenance_secret` mutiert (z.B. via Reload-Hook ohne Re-Validierung), entstehen wieder ungesiegelte Rows. **Mitigation:** Settings sind Pydantic-immutable post-init, Reload-Hooks existieren nicht im aktuellen Code. Laufzeit-Mutation ist nur durch Patch denkbar — Code-Review-Konvention.
- **B-002 Observability-Capability-Probe ist hardcoded `False`** — sobald `get_daily_operator_summary()` echte Werte für `llm_provider_failure_rate_24h` und `rss_to_alert_latency_p95_seconds_24h` liefert (statt `not_implemented`-String), muss `_enforce_re_entry_invariants` die Probe-Logik nachziehen. Heute reicht der Opt-In-Default.
- **E2E-Heartbeat-Test fehlt** — Worker-Touch + periodischer Loop sind Unit-getestet, der `run_worker`-Pfad ist Telethon-abhängig und nur durch echten Restart verifizierbar. Erste Verifikation passiert beim ersten Pi-Server-Restart.
- **`/status`-Surface zeigt re_entry_mode noch nicht** — Follow-up: `get_daily_operator_summary` um `re_entry_mode_enabled: bool` und `re_entry_mode_invariants_ok: bool` ergänzen, damit Operator den Zustand ohne `.env`-Diff sieht.

---

## 7. Querverweise

- D-202 (DECISION_LOG.md) — Implementation (Settings-Validator + Runbook)
- D-201 (DECISION_LOG.md) — Heartbeat-Surface in `canonical_read.py` + Worker-Heartbeat-Helper
- D-200 (DECISION_LOG.md) — `pi_transfer_artifacts.sh` database-group + ssh-tunnel
- D-191 (DECISION_LOG.md) — Replay-Cache-DB im Pfad-Inventar
- `docs/pi_migration/preflight.md` — Pi-Setup, systemd-Units, DNS/Tunnel-Switchover
- Memory `project_tv_pivot.md` — Re-Entry-Kriterien (≥200 resolved alerts ODER ≥10 paper fills)
- Memory `reminder_server_migration_pi.md` — Migrations-Logistik
