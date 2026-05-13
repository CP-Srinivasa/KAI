# Phase-0 Step 12 — Tabletop-Drill (Compromise-Simulation)

**Stand:** 2026-05-13 · **Status:** Drill-Skript verbindlich vor Live-Aktivierung. · **Aufwand:** ~2h Solo-Operator · **Voraussetzung:** Tasks 1-11 aus `kai_light_live_phase0_spec.md` (alle PRs #4/#6/#7/#8 + PRE-Tests #9 gemerged + Pi 5 deployed).

---

## Ziel

Bevor der `LIVE_TRADING_DEFAULT_ENABLED`-Flag jemals von `False` auf `True` wechselt (oder bevor der erste `/live unlock`-HOTP-Code in Production akzeptiert wird), **muss** dieser Drill durchgespielt + protokolliert sein. Sieben Compromise-Szenarien, ein Migration-Drill — jedes mit klarem Pass/Fail-Kriterium. Jeder Fail blockiert Live-Aktivierung bis Fix.

Der Drill ersetzt **keinen** Penetrationstest. Er ist die *Operator-Selbstverifikation* der 5-Layer-Defense aus dem Phase-0-Spec: HOTP-Auth + Cap-Check + Risk-Engine + Exchange-Perm + Server-SL.

## Setup vor Beginn

```bash
# 1. Schalte SICHER, dass paper-mode aktiv ist (kein live-Risiko bei Versehen):
grep -i 'LIVE_TRADING_DEFAULT_ENABLED' ~/.env
# erwartet: nicht gesetzt ODER =False

# 2. Smoke-Check-Script läuft auf aktuellem HEAD:
python -m scripts.security.tabletop_drill_checks

# 3. Operator hält bereit:
#    - Authenticator-App auf 2 Geräten (Smartphone + Tablet) mit aktivem HOTP-Seed
#    - QR-Backup-Print im Tresor erreichbar (Recovery-Test S-Drill-A)
#    - SSH-Zugriff auf Pi 5 + Pi 4b (Standby für Migration-Drill)
#    - Binance-Testnet-Subaccount mit ~$100 USDT, Withdraw=OFF, IP-Allowlist=Pi-WAN
#    - Bybit-Testnet-Subaccount analog
#    - Tabletop-Log offen: `artifacts/security/tabletop_drill_log_<YYYY-MM-DD>.md`
#      (Template am Ende dieses Dokuments)
```

**Konvention:** Jedes Szenario unterhalb hat 4 Blöcke — *Threat-Stage*, *Drill-Step*, *Expected*, *Pass-Kriterium*. Operator führt den Drill-Step physisch aus, beobachtet, vergleicht mit Expected, hakt im Log ab.

---

## Szenario 1 — SSH-Key-Diebstahl + Cap-Bypass-Attempt (S-002)

**Threat-Stage:** Angreifer hat SSH-Zugriff auf Pi (Laptop kompromittiert, Key extrahiert). Angreifer will Live-Order >$200 platzieren.

**Drill-Step:**
1. SSH auf Pi: `ssh ubuntu@192.168.178.23`
2. Versuche, das Cap zu umgehen via direktem Modul-Patch:
   ```bash
   sed -i 's/MAX_POSITION_USD: float = 200.0/MAX_POSITION_USD: float = 100000.0/' \
     /home/ubuntu/ai_analyst_trading_bot/app/security/live_caps.py
   # systemctl restart kai-server.service   # NICHT ausführen — nur als "wenn er das täte"
   ```
3. **Wichtig:** Setze die Datei sofort zurück: `git checkout -- app/security/live_caps.py`.
4. Verifiziere danach mit `python -m scripts.security.tabletop_drill_checks --component live_caps`.

**Expected:**
- Schritt 2 ist trivial mit SSH-Schreibrechten ausführbar — daher S-002.
- Mitigation: nach Restart würde die zweite Layer (RiskEngine + Position-Notional-Check in `app/risk/engine.py`) den $100k-Trade trotzdem ablehnen (Standard-Risk-Limits).
- Der Audit-Stream `live_execution_audit.jsonl` würde im Real-Szenario eine `live_caps_check: passed` UND eine `risk_engine_check: rejected`-Spur zeigen — Forensik-tauglich.

**Pass-Kriterium:**
- [ ] `tabletop_drill_checks --component live_caps` bestätigt nach Restore: `MAX_POSITION_USD=200.0` (hardcoded constant unchanged).
- [ ] `app/risk/engine.py` enthält einen Position-Notional-Limit-Check, der unabhängig von `live_caps` läuft (Re-Verify Code-Review).
- [ ] Phase-1-Backlog enthält Eintrag „IPC-Process für RiskEngine" (zweiter Pfad gegen S-002).

---

## Szenario 2 — HOTP-Seed-Leak + Replay-Attempt

**Threat-Stage:** Angreifer kennt HOTP-Seed (Authenticator-Backup-Datei gestohlen). Versucht, `/trade BTCUSDT buy 0.001 <code>` zweimal mit demselben Code zu senden.

**Drill-Step:**
1. Auf Authenticator-App: notiere drei aufeinanderfolgende HOTP-Codes (C1, C2, C3 für Counter N, N+1, N+2).
2. Schicke `/trade BTCUSDT buy 0.001 C1` via Telegram → erste Order. (Paper-mode, kein realer Cap-Risk.)
3. Schicke sofort `/trade BTCUSDT buy 0.001 C1` zum zweiten Mal — exakt selber Code.
4. Schicke `/trade BTCUSDT buy 0.001 C3` (überspringt C2 → out-of-window).
5. Prüfe `artifacts/security/hotp_counter.jsonl` — counter darf nur monoton steigen.

**Expected:**
- Schritt 2: Order durch (HOTP-verify ok, Cap ok, Risk ok, Exchange-perm ok, Order placed).
- Schritt 3: Telegram-Bot antwortet `hotp_replay_rejected` — Order nicht gesendet.
- Schritt 4: Telegram-Bot antwortet entweder `hotp_accepted` (wenn 3-Code-Tolerance-Fenster reicht) oder `hotp_out_of_window`. Beides ist OK, solange counter dann auf N+2 steht.
- `hotp_counter.jsonl` zeigt monoton: `N, N+1` (oder `N, N+2` wenn step 4 akzeptiert), niemals zweimal `N`.

**Pass-Kriterium:**
- [ ] Doppel-Submit von C1 wird **zwingend** abgelehnt (Counter-Replay-Schutz).
- [ ] Counter im JSONL ist monoton (Re-Read nach Drill: kein duplicate counter-value).
- [ ] Telegram-Bot-Response enthält `reason=hotp_replay_rejected` (Forensik-String).

---

## Szenario 3 — Pi-Crash mit offener Position (S-003)

**Threat-Stage:** Pi 5 verliert Strom oder kernel-panics direkt nachdem eine Live-Order platziert wurde. Position ist offen, KAI-Process tot.

**Drill-Step (Testnet, NICHT Mainnet):**
1. Platziere im Binance-Testnet eine Limit-Buy + Server-SL via `app/execution/exchanges/binance.py::place_oco`.
2. Verifiziere am Exchange (Web-UI), dass *beide* Orders existieren — main + SL.
3. Stoppe kai-server hart: `sudo systemctl kill -s SIGKILL kai-server.service`.
4. Senke Markt-Preis künstlich (im Testnet-UI: Manual-Price-Set, oder wenn nicht: warte auf realen Trigger).
5. SL feuert am Exchange → Position closed. Verifiziere am Exchange-Web-UI.
6. Starte kai-server neu: `sudo systemctl start kai-server.service`.
7. Lass position-monitor laufen: `python -m app.cli.main trading position-monitor-once`.
8. Verifiziere `artifacts/live_execution_audit.jsonl` enthält ein `position_closed_server_sl`-Event.

**Expected:**
- Schritt 2: Main + SL beide live am Exchange.
- Schritt 5: SL schließt unabhängig von KAI.
- Schritt 7-8: KAI-Recovery erkennt den Close, schreibt Audit-Trail, kein orphaned position.

**Pass-Kriterium:**
- [ ] OCO-Order ist atomar gespeichert (beide IDs in `live_execution_audit.jsonl::live_order_placed`-Event).
- [ ] Position-Close passiert ohne KAI-Mitwirkung (Exchange-Web-UI zeigt Status `FILLED` für SL).
- [ ] KAI-Recovery nach Restart schreibt einen `position_closed`-Event mit `source=exchange_server_sl` (oder analogem Marker).
- [ ] Kein „offene Position laut KAI, geschlossene Position laut Exchange"-Drift in `audit_replay.diff`.

---

## Szenario 4 — Operator vergisst /live lock (Idle-Lock)

**Threat-Stage:** Operator unlockt Live-Mode um 09:00, vergisst zu locken, schließt Laptop, geht ins Wochenende.

**Drill-Step:**
1. `/live unlock <hotp>` via Telegram um T=0.
2. Mache eine Live-Order: `/trade BTCUSDT buy 0.001 <hotp>` um T=10min (Paper-mode).
3. Setze keine weiteren Trades. Warte bis T=61min (oder mocke die Uhr via `LIVE_MODE_IDLE_LOCK_SECONDS=120` für 2-Min-Test in einer separaten test-instance).
4. Versuche `/trade BTCUSDT buy 0.001 <hotp>` um T=62min.

**Expected:**
- T=62min: Bot antwortet `live_mode_locked_idle_timeout`. Order nicht gesendet.
- Operator muss `/live unlock <neuer hotp>` erneut eingeben.

**Pass-Kriterium:**
- [ ] Idle-Lock greift nach exakt `LIVE_MODE_IDLE_LOCK_SECONDS` (default 3600).
- [ ] Lock-Status persistiert über kai-server-Restart (Audit-State, nicht nur Memory).
- [ ] Telegram-Notify an Operator beim Auto-Lock (proaktive Information).

---

## Szenario 5 — Exchange-Permission-Drift (Withdraw versehentlich aktiviert)

**Threat-Stage:** Operator klickt in Binance-Web-UI versehentlich „Enable Withdrawal" an. KAI muss das innerhalb 30min erkennen + sofort lock.

**Drill-Step (Testnet):**
1. Auf Binance-Testnet-Account: API-Key-Settings → Withdraw aktivieren.
2. Warte den nächsten Period-Re-Check-Tick ab (default alle 30min, oder force-trigger via CLI `python -m app.cli.main trading verify-exchange-perms`).
3. Beobachte `kai-server.log` + Telegram-Bot.

**Expected:**
- Re-Check erkennt `enableWithdrawals=true` → `LiveModePermLock("withdraw_drift")` raises.
- Telegram-Bot sendet HIGH-PRIORITY-Notify an Operator.
- Live-Mode-Flag wird auf locked gesetzt, alle pending `/trade` werden abgewiesen.

**Pass-Kriterium:**
- [ ] Re-Check ist scheduled UND CLI-triggerbar (zwei Pfade).
- [ ] Lock greift sofort (kein „nächster Trade wird abgelehnt", sondern „Mode ist lock-ed").
- [ ] Audit-Eintrag in `live_execution_audit.jsonl` mit `event=permission_drift_detected, drift=withdraw_enabled`.
- [ ] Recovery-Pfad dokumentiert: Operator setzt Withdraw zurück, manuell `/live unlock <hotp>` re-aktiviert nach Re-Check-Pass.

---

## Szenario 6 — Server-SL-Placement-Failure (OCO-Atomicity)

**Threat-Stage:** Binance akzeptiert Main-Order aber lehnt SL-Order ab (z.B. wegen Margin-Drift, Symbol-Restriction, oder Exchange-Bug). Position wäre offen ohne Schutz.

**Drill-Step (Testnet):**
1. Wähle ein Symbol mit min-notional, das die Main-Order gerade noch akzeptiert, aber wo die SL bei einem absichtlich falschen Trigger-Price scheitert (z.B. Stop-Price oberhalb der Bid bei BUY → Binance lehnt ab).
2. Sende über `place_oco` mit gewählter falscher SL → erwarte Failure.
3. Verifiziere am Exchange-Web-UI, dass keine offene Position existiert.

**Expected:**
- `place_oco` raises `LiveOrderRejected("sl_placement_failed")`.
- Best-effort-Cancel der Main-Order läuft automatisch (`exchange.cancel(result.main_order_id)`).
- Position-Monitor findet beim nächsten Tick keine orphaned position.

**Pass-Kriterium:**
- [ ] LiveOrderRejected wird gehoben, kein silent-fail.
- [ ] Audit-Eintrag: `live_order_failed` mit `reason=sl_placement_failed, main_order_cancelled=true`.
- [ ] Im worst-case (Main-Order fillte BEVOR SL-Placement scheiterte): Audit-Event `orphaned_position_detected`, Telegram-Notify, Operator muss manuell schließen.

---

## Szenario 7 — OCO-Race auf Binance (Main fillt vor SL-Placement)

**Threat-Stage:** Marktorder oder Limit-Order auf der Kante. Main-Order fillt sofort. SL kommt erst Sekunden später am Exchange an.

**Drill-Step (Testnet):**
1. Modus Market-Order: `/trade BTCUSDT buy 0.001 <hotp>` mit Limit-Price genau am Best-Bid → instant fill wahrscheinlich.
2. Beobachte Timing in `live_execution_audit.jsonl`:
   - `live_order_placed` zeigt `main_filled_at` vs `sl_placed_at`.
3. Falls Main fillt **vor** SL ankam: simuliere SL-Reject (analog Szenario 6) UND offene Position.

**Expected:**
- Time-Delta Main↔SL < 500ms im Normal-Case.
- Wenn SL nach Main-Fill scheitert: `orphaned_position_detected`-Audit + sofortige Telegram-Notify.

**Pass-Kriterium:**
- [ ] Audit-Schema speichert beide Timestamps für Forensik.
- [ ] Race-Detection-Code in `place_oco` (oder caller) erkennt orphaned-position + alarmt.
- [ ] Operator-Runbook enthält Punkt: „Bei `orphaned_position_detected`: sofort manuell SL setzen oder Position schließen, BEVOR weitere `/trade`-Commands."

---

## Szenario 8 — Pi-Migration-Drill (Pflicht-Gate)

**Threat-Stage:** Pi 5 muss innerhalb 1h auf alternativer Hardware (Pi 4b oder Cloud-VM) wieder laufen.

**Drill-Step:**
1. Stoppe kai-server auf Pi 5: `sudo systemctl stop kai-server.service` (alle services).
2. Auf Pi 4b (Standby): laufe `scripts/security/migration_drill.sh`:
   - clone Repo aktuellen HEAD
   - `pip install -e .` (Python 3.12+ vorhanden)
   - `.env` aus Backup deployen (HOTP-Seed-Backup, API-Keys via Vault-Restore)
   - `python -m scripts.security.tabletop_drill_checks --strict` läuft grün
   - kai-server starten, /live status via Telegram funktioniert
3. Stoppe Pi 5 nicht wieder hochfahren — alle Aktionen müssen über Pi 4b passieren.
4. Stop-Watch: Zeit von „Stoppe Pi 5" bis „Pi 4b /live status grün". Ziel: ≤ 1h.

**Expected:**
- migration_drill.sh läuft zu Ende ohne manual fixes.
- HOTP-Counter ist nach Restore identisch zum letzten Pi-5-Counter (Counter-State via Backup übertragen).
- Live-Mode bleibt nach Migration noch locked, Operator muss explizit `/live unlock` neu eingeben.

**Pass-Kriterium:**
- [ ] migration_drill.sh existiert und läuft idempotent.
- [ ] Time-to-recovery ≤ 1h.
- [ ] HOTP-Counter-State migriert (kein Replay-Risk durch Counter-Reset).
- [ ] Operator-Runbook `kai-live cutover-to-pi4b.md` ist auf aktuellem Stand (Letztes Update ≤ 30 Tage).

---

## Drill-Abschluss-Protokoll

```markdown
# Tabletop-Drill-Log <YYYY-MM-DD>

Operator: sascha_german@hotmail.de
Code-HEAD: <git rev-parse HEAD>
Audit-Start: <UTC>

| Szenario | Pass | Notizen / Fail-Reasons | Follow-up-Ticket |
|---|---|---|---|
| 1 SSH-Cap-Bypass | ☐ | | |
| 2 HOTP-Replay | ☐ | | |
| 3 Pi-Crash + Server-SL | ☐ | | |
| 4 Idle-Lock | ☐ | | |
| 5 Permission-Drift | ☐ | | |
| 6 SL-Placement-Failure | ☐ | | |
| 7 OCO-Race | ☐ | | |
| 8 Migration-Drill | ☐ | | |

## Gate-Decision

- [ ] **PASS** — alle 8 Szenarien grün. Live-Aktivierung freigegeben.
- [ ] **CONDITIONAL** — N grün, M offen. Live-Aktivierung blockiert bis Follow-ups close.
- [ ] **FAIL** — fundamentale Lücke. Re-Drill nach Architektur-Fix.

Audit-Ende: <UTC>
Signatur: <Operator>
```

Das Protokoll-File landet im `artifacts/security/`-Pfad und wird zur Phase-0-Activation-Evidence.

---

## Was dieser Drill NICHT abdeckt (ehrlich)

- **Sub-Account-KYC-Drift:** falls Operator-Sub-Account-Limits sich ändern (Binance ändert Tier-Settings). Separater Operator-Monitor.
- **Withdraw-Permission-Bypass durch Exchange-API-Bug:** Exchange-seitige Bugs sind außer Kontrolle. Mitigation = Hardware-Cap am Sub-Account ($10k transferred, Main bleibt unberührt).
- **YubiKey-OATH-HOTP-Drift (Phase 1):** dieser Drill ist app-basierter Authenticator. YubiKey-Migration ist eigener Drill in Phase 1.
- **Multi-Tenant-Compromise:** KAI ist Single-Operator. Falls je zweiter Operator hinzukommt, Drill erweitern um Operator-Trennung.
- **Long-Tail-Network-Failures:** Cloudflared-Tunnel-Drop, DNS-Hijack, etc. — separates Netzwerk-Drill (nicht Live-Trading-Scope).

Diese Lücken sind in Phase-1-Backlog dokumentiert; sie blockieren Phase-0-Aktivierung **nicht**, weil das Hard-Cap ($200/pos × 2 pos) den Worst-Case auch in diesen Szenarien bei ≤$400 Schaden begrenzt.
