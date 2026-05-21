# Pi systemd-Timer Health-Probe — Spec 2026-05-21

**Auftrag:** DS-20260521-C (P2). Spec für nächtliche Probe, die inaktive `kai-*`-Timer aufdeckt. Auslöser: `kai-auto-annotate.timer` war 9 Tage 1h tot, unbemerkt (siehe `[[kai-auto-annotate-reactivation-20260521]]`). Bestehender Pin `[[feedback-post-deploy-smoke-mandatory]]` deckt nur `failed`/`inactive`-services ab, NICHT inactive-Timer (≠ failed).

**Status:** Spec-only, keine Implementation. Operator-Sign-off + Codex-Folge-Sprint nötig.

**Reviewer:** Claude Code (AI_HANDOFF §7 Verantwortlich für Doku-Track).

---

## 1. Problemstellung

`systemctl status kai-auto-annotate.timer` zeigte `Active: inactive (dead) since Tue 2026-05-12 12:11:04 CEST`. Das ist KEIN `failed`-State — der Timer wurde sauber gestoppt (vermutlich durch `systemctl daemon-reload`-Loop im 12.05.-E2E-Fix-Sprint).

**Konsequenz:** Pipeline A (`auto_annotator.py` mit Vol-/Window-Scaling) lief 9+ Tage nicht. Pipeline B (Fallback-CLI mit fix 5.0%-Threshold) lief weiter und produzierte 7758/8173 backfill-inconclusives — Lern-Stack atmete dünn.

**Root-Cause:** Bestehende Healthchecks (`/health/premium_pipeline`, `kai-service-watchdog.timer`) prüfen `service`-state, nicht `timer`-state. Inaktive Timer fallen durch das Raster.

## 2. Spec — Was die Probe tut

**Pfad:** Pi-internes Skript `scripts/pi_timer_health_probe.sh`, getriggert via neuen Timer `kai-timer-health-probe.timer` (täglich 04:30 UTC, vor Daily-Strategy-Bootstrap 06:00).

**Logik (Pseudo):**

```bash
#!/bin/bash
# scripts/pi_timer_health_probe.sh
# Listet alle kai-*-Timer + filtert auf nicht-active.
# Schreibt audit + sendet operator-bot-ping bei findings.

OUTPUT=$(systemctl list-timers --all --no-pager kai-*.timer | tail -n +2 | head -n -2)
NON_ACTIVE=$(echo "$OUTPUT" | grep -v "^[A-Z]" | awk '$8 !~ /active/')

if [ -n "$NON_ACTIVE" ]; then
    # JSONL-Audit
    ts=$(date -u +%Y-%m-%dT%H:%M:%S+00:00)
    echo "{\"timestamp_utc\":\"$ts\",\"event\":\"timer_health_probe.findings\",\"findings\":$(echo "$NON_ACTIVE" | jq -Rs .)}" \
        >> artifacts/timer_health_audit.jsonl

    # Operator-Bot-Ping via existing Telegram-Bot-Token
    BODY="⚠️ KAI Timer-Health: inactive timer(s) detected:\n\`\`\`\n$NON_ACTIVE\n\`\`\`"
    # (Aufruf an bestehende Operator-Bot-Send-Function — siehe app/messaging/telegram_bot.py)
fi
```

**Systemd-Unit (NEU, separat anzulegen):**

```ini
# /etc/systemd/system/kai-timer-health-probe.service
[Unit]
Description=KAI systemd Timer Health Probe (DS-V-C 2026-05-21)
After=kai-server.service

[Service]
Type=oneshot
User=ubuntu
Group=ubuntu
WorkingDirectory=/home/kai/ai_analyst_trading_bot
ExecStart=/home/kai/ai_analyst_trading_bot/scripts/pi_timer_health_probe.sh
StandardOutput=journal
StandardError=journal
```

```ini
# /etc/systemd/system/kai-timer-health-probe.timer
[Unit]
Description=Trigger KAI Timer Health Probe daily at 04:30 UTC
Requires=kai-server.service

[Timer]
OnCalendar=*-*-* 04:30:00 UTC
AccuracySec=2min
Unit=kai-timer-health-probe.service

[Install]
WantedBy=timers.target
```

## 3. Akzeptanzkriterien

- [ ] Probe schreibt JSONL-Audit `artifacts/timer_health_audit.jsonl` bei findings (per-event-shape mit timestamp_utc + findings-array).
- [ ] Probe sendet Telegram-Bot-Ping an Operator-Chat bei findings (kein Spam bei "alle grün").
- [ ] Probe läuft idempotent (mehrfacher Lauf erzeugt KEINEN false-positive-Alarm wenn nichts neu inaktiv ist — letzten Stand vergleichen).
- [ ] Probe-Skript respektiert `KAI_TIMER_PROBE_DRY_RUN=1` (kein bot-ping, nur audit) für Tests.
- [ ] Bei zukünftigen Service-Restarts: existing `--reactivate`-Skript prüft Timer-State + reaktiviert inaktive (siehe Folge-Punkt §6).

## 4. Out-of-Scope

- **Service-Reaktivierung durch Probe:** Probe meldet nur, repariert nicht. Operator-Decision pro Findung.
- **General "alle Pi-Services healthy"-Probe:** das ist `kai-service-watchdog.timer`-Domäne, NICHT diese Spec.
- **Cross-Pi-Monitoring (z.B. 2 Pis):** nicht relevant für Single-Pi-Setup.

## 5. Risiken

- **Telegram-Bot-Token-Verfügbarkeit:** wenn `OPERATOR_TELEGRAM_BOT_TOKEN` nicht gesetzt, Probe sollte fail-graceful (nur audit, kein Ping). 
- **Idempotency:** ohne State-Tracking würde Probe TÄGLICH bei demselben inaktiven Timer pingen → Operator-Notification-Spam. **Mitigation:** Probe vergleicht aktuelle findings gegen letzten Run (`artifacts/timer_health_audit.jsonl` tail) und pingt nur bei NEW findings ODER alle 7 Tage als "still-broken"-Reminder.
- **systemd-list-timers-Output-Format-Drift:** wenn systemd das Tabellen-Format ändert (selten), bricht der awk-Filter. **Mitigation:** Probe nutzt `--output=json` wenn verfügbar.

## 6. Folgepunkte (NICHT in dieser Spec)

- **Service-Restart-Skript `scripts/pi_install_systemd.sh --reactivate` erweitern:** nach jedem reactivate-Run ZUSÄTZLICH alle `kai-*.timer` enabled + active checken + ggf. nachstarten. Eigenes ARBEITSPAKET (P2-Folge).
- **Daily-Strategy-Bootstrap-Hook:** beim morgendlichen Bootstrap die letzten 24h Timer-Health-Probe-Findings im Header anzeigen. P3-Komfort.

## 7. Implementierungs-Empfehlung an Codex

ARBEITSPAKET-Format wenn Operator Sign-off:
- task_id: V-C-IMPL
- in_scope: `scripts/pi_timer_health_probe.sh`, `deploy/systemd/kai-timer-health-probe.service`+`.timer`
- out_of_scope: bestehende `--reactivate`-Logic, kai-service-watchdog
- Tests: 1 Unit-Test für Probe-Output-Parsing + Mock-systemctl + Idempotency-Vergleich
- Doku-Sync: ARCHITECTURE.md Known-Limits Zeile zu Audit-Stream-Rotation ergänzen falls neuer Stream `timer_health_audit.jsonl` rotation braucht
- Akzeptanzkriterien siehe §3

## 8. Querverweise

- `[[feedback-post-deploy-smoke-mandatory]]` — lückenhaft bei inactive (≠ failed)
- `[[kai-auto-annotate-reactivation-20260521]]` — konkreter Anlassfall
- `docs/AI_HANDOFF.md` §5 Post-Deploy-Smoke-Update
- Existing patterns: `kai-service-watchdog.timer` (5min, service-state), `kai-premium-healthcheck.timer` (60s, pipeline-health)
- Operator-Bot-Send: `app/messaging/telegram_bot.py`
