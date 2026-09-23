# KAI System-Audit 2026-09-16 — Architektur, Health, Auffälligkeiten (Laptop · Pi-Ubuntu · Pi-Lightning)

**Stand:** 2026-09-16 ~13:00Z · **Read-only**, keine Änderung an Runtime, Mainline oder Node.
**Basis:** Mainline `84ce1e26` (Laptop-Checkout), Pi-Runtime `e3011a58` (Release seit 14.09. 16:21Z), RaspiBlitz-Node „KAI" (nur über den Info-Pfad des Pi und KAIs eigene Read-Endpunkte; keine Node-Shell).
**Methode:** zwei Rohdaten-Schnappschüsse (Pi 1 117 Zeilen, Laptop 305 Zeilen, Session-Scratchpad `audit/`), danach sechs Fachanalysen der Roster-Agenten (Watchdog, Architect, SENTR, Satoshi, Neo, Data-Quality-Inspector; je 13–15 Findings append-only in `artifacts/agents/<slug>/findings.jsonl`), anschließend Gegenprüfung der P0/P1-Kernaussagen durch mich am Gerät. Jede Zahl unten ist gemessen oder mit Datei:Zeile belegt; Vermutungen sind als solche markiert.

---

## 0. Lagebild in fünf Sätzen

1. **Der Betrieb läuft, und die Wertkerne halten:** alle Timer feuern planmäßig, `/health` ohne Drift, Sendepfad mehrfach verriegelt (Flag, shadow, leere Allowlist = deny, Caps, HOTP), Secret-Validierung fail-closed, CI mit Secret-Guard, Truth-Kette und Zahlungsjournal hash-verkettet.
2. **Zwei P0 liegen außerhalb des Codes, den die Tests sehen:** das Static-Channel-Backup des Lightning-Nodes ist 74 Tage alt, nichts erzeugt es, der Sync scheitert seit dem 23.07. bei jedem Lauf, und beide Wächter sind stumm; und `/health` wird mit rund 20 Anfragen pro Sekunde aus dem eigenen Netz gepollt, Ursache ist eine Re-Subscribe-Schleife im Dashboard-Hook, die auch im morgigen Deploy-Ziel enthalten ist.
3. **Der Health-Kanal ist entwertet:** rund 48 Telegram-CRITICAL/Recovery-Meldungen pro Tag ohne reales Ereignis, weil das laufende Release `kai-litellm` noch als „deferred" führt und `kai-entry-watch` als 69-Sekunden-Takt gebaut ist; der Deploy auf `84ce1e26` beendet beides, löst aber die Ursache des zweiten nicht.
4. **Die Peripherie ist die Schwachstelle:** SSH mit Passwort-Auth ohne fail2ban, `pay.kai-trader.org` ohne Cloudflare Access mit Klartext-Ingress ins LAN, 80 Klartext-Kopien der `.env` mit Vor-Rotations-Secrets, ein undokumentiertes Macaroon, sechs ausstehende Kernel-Builds bei 112 Tagen Uptime, 4 GB Journal ohne Grenze, 200 MB App-Logs pro Tag auf der SD-Karte.
5. **Architektur und Daten sind tragfähig, aber mit bekannten Schulden:** Schichtung hält (nur 4 Aufwärtskanten, alle lazy), Ratchet deckt jedoch nur 9 von 23 großen Dateien, der Orchestrator ist mit Test-Verhältnis 0,12 der schwächst getestete Kern, Kostensummen aus der Telemetrie verdoppeln sich ohne Ebenenfilter, 70 % der Paper-Fills tragen kein Klassenlabel, und die Prioritätsspalte fehlt bei 100 % der TradingView-Alerts.

---

## 1. Befunde nach Schweregrad (dedupliziert über alle Ebenen)

Spalten: ID(s) der Agenten-Dropboxen · Ebene · Beleg · Wirkung · Maßnahme · Aufwand.

### P0

| # | ID | Ebene | Befund und Beleg | Wirkung | Maßnahme | Aufwand |
|---|---|---|---|---|---|---|
| P0-1 | SA-A-001, SA-A-002, SA-A-004 | Pi-Lightning + Laptop | Letztes Off-Node-SCB `channel-20260704-165017.backup` (756 B, sha256 `f9795058…`), letzter erfolgreicher Sync 15.07. Laptop-Task `KAI-SCB-Pull-Hourly` scheitert seit 23.07. bei jedem Lauf: `sync-scb.log` 234× FAIL / 1× OK, aktuell „Permission denied (publickey,password)" gegen `admin@192.168.178.51`; Alarm nur als `KAI-mirror\_ALERT_SCB_STALE.txt`. Auf dem Pi: kein Treffer für `channels/backup` in `app/`+`scripts/`, Unit `kai-ln-scb-backup` existiert weder im Repo noch auf dem Gerät; `kai-ln-scb-monitor` meldet `not_configured` (`APP_LN_SCB_PATH` leer) und alarmiert per Design nicht. Node hält heute 1 aktiven Kanal (local 370 739 sat, remote 28 281 sat), Wallet 1 548 197 sat on-chain. | Bei Node-Verlust ist die Wiederherstellbarkeit des Kanals seit 74 Tagen unbekannt, nicht gegeben. Beide Wächter, die das melden sollen, sind blind. | **Heute (Operator, Node-Shell):** `sha256sum` + `stat` des `channel.backup` gegen `f9795058…`, `lncli listchannels/closedchannels/pendingchannels`; ergibt sich eine Abweichung, sofort manuell exportieren und off-node sichern. **Sprint S2-1:** Read-only-Exporter über `GET /v1/channels/backup` (braucht nur `offchain:read`, `readonly.macaroon` vorhanden) + Timer + Alarm auf `send_operator_notification`; Laptop-Task auf Pi-REST-Pfad umstellen oder abschalten. | Operator 15 min; Sprint moderat (1 Tag) |
| P0-2 | NEO-A-009, DQ-A-006, SE-A-015 + eigene Messung | Pi-Ubuntu ← Laptop | `GET /health` mit **~20 Anfragen/s** von **einer** IP: 203 Logzeilen in 10 s (13:05Z), 159 526 der letzten 200 000 `server.log`-Zeilen; `api_request_audit.jsonl` wächst ~44 000 Zeilen/h (99,9 % `/health`), `server.log` +108 MB/Tag, `server.err.log` 101 MB/Tag (1,25 Mio Zeilen seit Mitternacht), Latenzfenster am Deckel (`kai_http_requests_window_total{route="/health"}=5000`). **Quelle belegt:** die IP `94.114.206.90` ist die öffentliche Adresse dieses Laptops, und auf ihm läuft ein Chrome-Fenster „KAI — Control Center". **Ursache im Code:** `web/src/lib/useBackendHealth.ts:72-76` übergibt `useSyncExternalStore` bei jedem Render eine **neue** `subscribe`-Funktion; React meldet ab und wieder an, `subscribe()` (Z. 55-60) pingt bei jedem 0→1-Übergang sofort, `emit` erzeugt ein neues Snapshot-Objekt, das Render löst die nächste Runde aus. Die Schleife ist nur durch die Netzlatenz begrenzt. | 1,8 Mio Anfragen/Tag auf einem Single-Worker-Pi, 200 MB Log pro Tag auf SD, Audit-Rotation viermal über Ziel, `/health`-Latenz max 1,73 s. **Das Deploy-Ziel `84ce1e26` enthält denselben Hook** — der Fehler fährt morgen mit. | **Sprint S1-1 (vor oder direkt nach dem Deploy):** `subscribe` stabil machen (modulweite Funktion je `pollMs` bzw. `useCallback`), `emit` nur bei Änderung, Mindestabstand zwischen Pings; Test mit Render-Zähler. Danach SPA neu bauen und übertragen (Ablauf wie am 16.09.). Zusätzlich (S2): `api_request_audit`-Rotation an die Rate koppeln oder `/health`-Selbst-Polling nicht auditieren. | S (3 Zeilen + Test, 1 h) + Rebuild/Transfer 20 min |
| P0-3 | DQ-A-001 | Daten (Pi) | `llm_telemetry.jsonl`: je `correlation_id` tragen `chain_position=0` (Aufruf) und `chain_position=-1` (Ketten-Summe) **denselben** `cost_usd` (Stichprobe 5/5, n = 2 747 Gruppen). Eine Summe ohne Ebenenfilter verdoppelt jede Kostenzahl (15.09.: roh 2,43 USD, tatsächlich 1,24). | Jede Ad-hoc-Kostenauswertung, Vorlage oder Digest-Zahl, die den Filter nicht kennt, ist um Faktor 2 falsch. Die Reserve-Auswertung vom 16.09. filtert korrekt; die Regel steht nur in zwei Skripten. | **Sprint S2-2:** ein Aggregations-Helper in `app/ai/spend.py` (oder Nachbar) mit erzwungener Ebenenwahl, Test mit Doppelzeile; alle Leser (Digest, Health, Dashboard) darauf umstellen; Leseregel in `docs/`. | S (0,5 Tag) |
| P0-4 | DQ-A-002 | Daten → Dashboard | `priority` fehlt in `alert_audit.jsonl` strukturell für `channel=tradingview_webhook` (0/3 301 Zeilen) gegenüber telegram 53,5 %, email 8,5 %; TV = 77 % der letzten 200 Zeilen. `recent_alerts` und die Dashboard-Karte lesen `r.get("priority")` → `None` für die Mehrheit. | Die neue Prioritäts-Spalte (v2.1) zeigt bei den meisten aktuellen Alerts „—"; Tier-Lift-Statistik zählt TV-Alerts nicht. | **Sprint S2-3:** Priorität im TV-Webhook-Pfad in den Audit-Record schreiben (Writer `app/alerts/audit.py`, TV-Router), Backfill nicht nötig; Karte zeigt bis dahin „ohne P (TV)" statt „—". | S (0,5 Tag) |

### P1

| # | ID | Ebene | Befund und Beleg | Maßnahme | Aufwand |
|---|---|---|---|---|---|
| P1-1 | SE-A-001 | Edge/Laptop-Netz → Node | `https://pay.kai-trader.org/` antwortet 200 **ohne** `Www-Authenticate` (Hauptdomain: 302 + Cloudflare-Access); `/admin`, `/wallet`, `/api/v1/health` → 403 vom Edge = Deny-Liste statt Default-Deny; Ingress laut `/home/kai/.cloudflared/config.yml`: `pay.kai-trader.org → http://192.168.178.51:5006` (Klartext-HTTP über LAN statt wg0). LNURL-pay `/.well-known/lnurlp/kai` = 200. | Default-Deny mit Allow nur für `/.well-known/lnurlp/*` und den Callback; Origin auf `10.27.0.51`. Operator (Cloudflare + config.yml). | moderat (2 h) |
| P1-2 | SE-A-002 | Pi-Ubuntu | sshd bietet `publickey,password` (Test mit `PubkeyAuthentication=no`), `PasswordAuthentication` nicht explizit gesetzt, fail2ban nicht installiert, Port 22 auf `0.0.0.0`/`[::]`; ufw-Regeln ohne sudo nicht lesbar. | `99-kai-hardening.conf` (`PasswordAuthentication no`, `PermitRootLogin no`) + fail2ban; Operator mit sudo. | minimal (20 min) |
| P1-3 | SE-A-003, WD-A-001 | Pi-Ubuntu | `/var/run/reboot-required` seit 12.09. (Kernel 1057–1064 gegen laufenden 1056, libc6), 46 Pakete offen, Uptime 112 d; unattended-upgrades rebootet nicht. | Reboot **nach** Deploy und **nach** `systemctl enable kai-litellm` (sonst bleibt LiteLLM aus); Dev-Proxy-tmux vorher in eine Unit (P2-4). | Operator 15 min + 10 min Ausfall |
| P1-4 | SE-A-004 + eigene Zählung | Pi-Ubuntu | **80** Dateien `.env.*` im Checkout (Mai–Sep, `-rw------- ubuntu`, 5–14 kB), Secrets von vor der E-1-Rotation und der Proxy-Rotation 14.09.; aktueller Satz 204 Schlüssel inkl. `LITELLM_MASTER_KEY`, `APP_PAYMENT_VAULT_KEY`, `KAI_BACKUP_PASSPHRASE`. | `shred -u` bis auf die letzten 3, Backup-Skripte auf verschlüsselte Ablage + Retention 3. Operator. | minimal (15 min) |
| P1-5 | SE-A-009, SA-A-006 | Pi-Lightning | `kai-secrets/lnd/kai-cockpit.macaroon` (154 B) steht in keiner Doku, in keiner `.env`-Variablen, in keinem Modul; Matrix nennt `kai-readonly.macaroon`, Platte hat `readonly.macaroon`; `kai-payment.macaroon` (91 B) ist kleiner als Invoice (140 B) trotz zwei Rechten. | Node: `lncli printmacaroon` je Datei (nur Ausgabe, nie Inhalt), Cockpit dokumentieren oder widerrufen, Matrix nachziehen. Operator + Doku-PR. | minimal |
| P1-6 | SA-A-005 | Pi-Lightning | Payment-Macaroon liegt schon auf dem Pi, obwohl der Sendepfad inert ist; HOTP-Seed (`kai-secrets/hotp_seed.b32`) im selben Baum unter demselben User → Freigabe ist kein zweiter Faktor. Schadensbild (SE-A-012): wer die Datei hat, spricht direkt mit lnd-REST an der App vorbei; Grenze = Macaroon-Scope + Kanalkapazität 370 k sat. | Payment-Macaroon bis zum Re-Arm vom Dateisystem nehmen (Runbook Schritt 1 legt es dann an), Seed off-host. Operator. | minimal |
| P1-7 | SA-A-007, SA-A-009 | Pi-Lightning | 1 Peer, 1 Kanal, Tor-only-URI = SPOF für Empfang und Sendepfad; Inbound 28 281 sat deckelt L402 auf ~2 400 bezahlte Aufrufe, danach dauerhaft 402 unbemerkt. | Zweiter Kanal/Peer **erst nach** P0-1; Schwellwert-Alarm auf `channel_remote_sat`. | moderat |
| P1-8 | SA-A-008 | Code (app/payments) | `available_balance_sat = channel.local + wallet.total` (`rails/lightning.py:138-140`, `policy.py:97-112`): Reserve-Floor 1 840 000 < 1 918 936 (Spielraum 78 936 sat), aber On-Chain-Guthaben trägt die Liquiditätsprüfung; 1 000 sat würden auch bei 100 sat Kanalguthaben freigegeben. | `spendable_offchain_sat` vom Gesamtbestand trennen. Sprint S2-4. | moderat (0,5 Tag) |
| P1-9 | WD-A-002, WD-A-003, WD-A-004, NEO-A-006 | Pi-Ubuntu | `kai-litellm.service` `disabled` (läuft nur seit Deploy-Start); Release `e3011a58` führt es in `DEFERRED_UNITS` → `deferred_unit_active` in 96/96 Health-Läufen; `kai-entry-watch` NRestarts 2 239 in 43 h, `process_runtime_marker` in 19/96 Läufen; 34 CRITICAL + 14 Recovery Telegrams/24 h ohne Ereignis. | Deploy `84ce1e26` (17.09.), danach `systemctl enable kai-litellm`, 24 h Nachbeobachtung (0916-V5). | im Deploy |
| P1-10 | NEO-A-001, NEO-A-002, NEO-A-004, NEO-A-005 | Code (entry-watch) | `--duration-seconds 55` + `Restart=always/5 s` = Takt 69 s: Restart-Zähler als Störungsanzeige tot, ~4,3 von 10 `StartLimitBurst`-Slots im Normalbetrieb verbraucht; `operator_entry_watch.py:564-565` liest je Tick beide Audit-Dateien vollständig (0,523 s CPU/Tick, 41 % der Zyklus-CPU bei `pending=0`); `app.cli.main` 2× je Zyklus importiert (2,0 s); keine `RuntimeMaxSec`/`WatchdogSec`, Deadline erst nach `await` → ein Hänger in `run_tick()` bleibt unsichtbar. #978 dämpft nur. | Sofort `RuntimeMaxSec=120` in der Unit (Zeile); Sprint S2-5: Dauerlauf **oder** oneshot+Timer, Fensterlesen (Offset merken statt Vollparse). | Zeile + moderat (1 Tag) |
| P1-11 | NEO-A-008, WD-A-011 | Pi-Ubuntu | uvicorn VmRSS 1,37 GB, VmHWM 1,50 GB, cgroup Peak 1,74 GB nach 43 h; 9×60 s flach → Plateau (Arena-Fragmentierung), kein Leck nachweisbar; Treiber-Hypothese: `hold_metrics.py:551` lädt drei Audits (22 MB) vollständig (91 MB roh je Lauf). Unit ohne `MemoryMax`/`MemoryAccounting`. | `MemoryMax=3G`+Accounting in der Unit (Schutz gegen OOM-Kollaps des Pi); 23:00Z-Cron um RSS-Zeile erweitern (Zeitreihe statt Schätzung); Sprint S3: Fensterlesen im Hold-Pfad. | minimal + moderat |
| P1-12 | NEO-A-010 + eigene Messung | Pi-Ubuntu | `/dashboard/api/quality` p50 10,17 s; JSON-Parse hält den GIL → Event-Loop-Lag max 4,11 s im Stundenfenster (`/metrics`). | Quality-Payload asynchron/prozessextern bauen oder Cache-Vorwärmen im Timer; gehört zu S3 mit P1-11. | moderat |
| P1-13 | DQ-A-003 | Daten | 5 `schema_version`-Generationen in `llm_telemetry.jsonl` (v1 77,9 % ohne `chain_position`/`cost_usd`/`budget_decision`); Voll-Aggregation verliert 78 % still. | Leseregel + Helper (mit P0-3); Archiv-Schnitt für v1-Zeilen. | S |
| P1-14 | DQ-A-004, DQ-A-005, DQ-A-010 | Daten | 70,6 % der `order_filled` ohne `trade_class`-Label (670 `technical_paper`-Fills, 0 Label-Events); 104 `order_created` pending ohne Fill, 3 Orphan-Fills; `message_id="dry_run"` als Sentinel (email 100 %, telegram 51,8 %) macht Dedup auf `message_id` blind. | Label-Event auch im technical_paper-Pfad schreiben; Sentinel durch `null` + `delivery_mode` ersetzen; pending-Orders mit Ablauf. Sprint S2-6. | moderat |
| P1-15 | AR-A-001 | Repo | 23 Dateien ≥ 1 000 LOC, nur 9 im Ratchet; 14 ungedeckelt (17 885 Zeilen). | Schwellen-Ratchet ab 1 200 LOC, `--update`. Sprint S2-7. | S |
| P1-16 | AR-A-002, AR-A-003 | Repo | Agenten-SSOT `_AGENTS` lebt im FastAPI-Router (`app/api/routers/agents.py`), importiert von `app/agents/worker.py:32` und `telegram_bot.py:1792`; `app/api/event_hub.py` (100 LOC, keine internen Importe) wird aus `execution`/`alerts` importiert. | `app/agents/registry.py`; `event_hub` nach `app/observability/`. Sprint S2-8. | moderat |
| P1-17 | AR-A-007, AR-A-008, AR-A-009 | Repo | God-Files: `cli/main.py` 2 431 (59 % alerts_*), `telegram_bot.py` 3 339 (eine Klasse, 85 Methoden), `dashboard.py` 3 004 (`_build_quality_payload` 467 Zeilen + `_load_*` 653 ohne FastAPI-Bezug). | Schnitte in eigene Module (Zielzahlen im Architect-Finding). Sprint S3. | high |
| P1-18 | AR-A-010 | Repo/Tests | Test-Verhältnis orchestrator 0,12 (3 579/444, 7 Testdateien), pipeline 0,20, cli 0,28 gegen core 2,13; KAI_CORE_V1 §4 nannte 0,18–0,23 — gefallen. | Testsprint Orchestrator (Loop-Schritte, Contradiction-Detektion, Cycle-Audit). Sprint S3. | high |
| P1-19 | eigene Messung, SE-A-015 | Pi-Ubuntu | Journal 4,0 GB ohne `SystemMaxUse`; `logs/` 357 MB mit vier Tagesdateien 39–101 MB (Rotation läuft, Volumen ist das Problem, siehe P0-2). | `journald.conf.d/kai.conf` `SystemMaxUse=1G`, Operator; Logvolumen fällt mit P0-2. | minimal |
| P1-20 | WD-A-009 | Konfiguration | Pi-`.env` 204 Schlüssel, `.env.example` deckt 121 (59 %); 137 fehlen dort (auch sicherheitsrelevante), 57 aus dem Example nicht gesetzt; Namensdrift `TRADINGVIEW_WEBHOOK_SECRET` vs. `TRADINGVIEW_WEBHOOK_SHARED_TOKEN`. | `.env.example` aus `settings.py` generieren (Namen, keine Werte) + Test, der Drift meldet. Sprint S2-9. | S |
| P1-21 | eigene Messung | Pi-Ubuntu | Drei Pfadkonventionen in den Units (`/home/kai/ai_analyst_trading_bot` 38×, `/home/kai/current` 19×, `/home/ubuntu/ai_analyst_trading_bot` 56×); nur 6 Daemons sind release-gebunden, alle Timer-Units laufen aus dem Checkout (`monitor-positions` läuft aus `/home/kai/.../.venv`). Runtime-Provenienz gilt also für den Server, nicht für die 50 Zeitjobs. | Entscheidung dokumentieren (bewusst Checkout für Timer?) oder Timer-Units auf `current/` heben; Pfad vereinheitlichen auf `/home/ubuntu`. Sprint S2-10. | moderat |

### P2 (Auswahl, vollständig in den Dropboxen)

- **Pi-Ubuntu:** 124 Skript-Leichen in `/tmp` (SE-A-008), 12 `.bak`-Unit-Dateien + unversionierter Drop-in `kai-server.service.d/stop-timeout.conf` (WD-A-008), Dev-Proxy 4001 als tmux-Prozess ohne Unit mit Master-Key im Env (SE-A-007), Unit-Härtung 12/63 `NoNewPrivileges`, standby-Units als root außerhalb des Release (SE-A-006), `memprobeB` User-Unit seit 01.09. hängend (eigene Messung), `kai-shadow-report-oneshot` seit 04.06. mit Exit 1 (WD-A-010), `dev.db.bak-pre0009` 196 MB unmanaged (DQ-A-009), `trading_loop_audit.jsonl` 95 MB ohne Rotation (DQ-A-007), Freshness-Liste ohne `llm_telemetry`/`api_request_audit` (DQ-A-008).
- **Code:** TOCTOU im Timer-Wächter `health_check.py:771-828` (zwei `systemctl show`-Aufrufe, NEO-A-007; heute nicht gefeuert); stille breite Handler `eligibility.py:693/:711` (Operator-Schwelle wird still durch Default ersetzt), `alerts/service.py:121` (Dedup-Seeding), `daily_briefing.py:207-296` (0 statt „nicht erhebbar") (NEO-A-012/013); nur 12 von ~90 JSONL-Append-Stellen mit `append_lock`, `bridge_pending_orders.jsonl` von drei Prozessen ohne Lock, PIPE_BUF-Begründung für reguläre Dateien falsch (NEO-A-014); Loop-Baseline-Eintrag für `run_watch_once` überholt (NEO-A-011); `client.py:169/172` `verify=False` bei leerem Pfad, heute durch Validator abgefangen (SE-A-010/SA-A-013); L402-Token 3 600 s ohne Nutzungszähler (SA-A-010); Reconcile-Timer grün trotz erwarteter Orphans, `ln_reconcile.py:66-71` Fenster (SA-A-012); 14 paketübergreifende Private-Importe (AR-A-004); `core` Fan-in 313 mit 8 Rückkanten (AR-A-006); `app/observability` 22 016 LOC, 44 % keine Ops-Health (AR-A-014, SPLIT seit 02.09. DEFER); MERGE truth/integrity/compliance → audit seit 02.09. unbewegt.
- **Laptop/Repo:** 114 lokale Branches (Ziel < 15), `.git` 193 MB mit 7 258 losen Objekten und 97-MB-Historien-Blob (AR-A-015); `ARCHITECTURE.md` (02.07.) mit 8 von 11 falschen LOC-Angaben (AR-A-011); Laptop-Backups AES-CBC ohne MAC, Schlüssel neben Ciphertext, `id_ed25519` ohne Passphrase (SE-A-005); `requirements.lock` ohne `--hash`, Dependabot nur npm (SE-A-014); Scheduled Task `KAI-PaperTrading` (Legacy, disabled) noch registriert; Blitz-Info-Pfad über LAN-IP mit `accept-new` (SE-A-011/SA-A-014).
- **Lightning-Node:** lnd 0.19.3-beta / bitcoind 29.2.0, 74 d ohne Reboot, Versionsbewertung ohne Advisory-Quelle bewusst nicht getroffen (SA-A-011); Allowlist-Inhalt nur als Hash, Selbstzahlungs-Kreisel möglich, wenn eigener Node gelistet (SA-A-015).

---

## 2. Was hält (Positivbefunde mit Messwert)

- Runtime-Provenienz: `/health` `runtime_commit == checkout_commit == e3011a58`, `drift_commits 0`, 0 failed units, nur 1 Journal-Zeile ≤ err in 24 h (WD-A-007/013).
- Ingress lebt: TV-Webhook letztes Event 09:39Z, Liquidations-Heartbeat sekündlich, alle Timer planmäßig, Ingress-Timer seit 21.08. durchgehend aktiv (WD-A-005/006/015).
- Sendepfad: `APP_LN_PAY_ENABLED=false`, `APP_PAYMENT_MODE=shadow`, Allowlist leer = deny (`policy.py:151-160`), Caps 1 000 sat, Approval ab 1 sat, `_build_client` verweigert Credential-Materialisierung; Geldfluss braucht kumulativ sechs Bedingungen (SE-A-012, SA-A-015).
- Journal und HOTP: hash-verkettet append-only mit Tip-Attestierung (`journal_chain.py:60-140`), monotoner Counter, Timeout → `UNKNOWN`/`RECONCILIATION_REQUIRED`, `fee_limit<=0` = DENY (Satoshi).
- Secrets: `APP_ENV=production` → `validate_secrets` fail-closed; `/metrics`, `/health/ai` verlangen Auth; Logging nur Fingerprint; kein `.env` in der Git-Historie; CI mit Secret-Guard, pip-audit, bandit; npm audit 0; pip check sauber (SE-A-013/014).
- Daten: `document_id`-Dedup sauber, 0 exakte Duplikate, Zeitstempel einheitlich `+00:00`, Alembic-Kopf `0009` = DB-Stand, Hot-Path-Indizes vorhanden (DQ-A-011/012).
- Architektur: 43 Pakete in 8 Schichten haltbar, nur 4 Aufwärtskanten (alle lazy), `exploration`/`governance` per Ratchet-Test isoliert, ADR 0017 eingehalten, CODEMAP 48/49 Anker gültig (Architect).
- Betriebsmittel: Platte 19 %, RAM 11,8 GB frei, kein Swap, 55 °C, `throttled=0x0`, NTP synchron.

---

## 2a. Stand der P0 (Nachtrag 17.09.2026)

| P0 | Stand | Beleg |
|---|---|---|
| P0-1 SCB 74 d alt, kein Erzeuger | **erledigt** — Off-Node-SCB vom Node verifiziert und byte-identisch zur Live-Datei, Laptop-Pull seit 16.09. 18:33Z wieder grün, alter Key-Eintrag am Node entfernt; Erzeuger auf dem Pi gebaut (Aktivierung offen) | §Nachtrag unten, PR #1003 |
| P0-2 `/health`-Flut 20/s | **behoben und deployt** — `useBackendHealth` stabil, Rate nach Deploy 0/10 s | #991, Runtime `6659f7ae` seit 17.09. 05:26Z |
| P0-3 Kostenverdopplung Telemetrie | **behoben (gemergt, nicht deployt)** — Budget und `/health/ai` entdoppelten bereits seit #970; einziger Produktivleser ohne Entdopplung war die Dashboard-Telemetrie (338 statt 185 Aufrufe, Fehlerquote halbiert) | #998 |
| P1-5 `kai-cockpit.macaroon` undokumentiert | **erledigt 17.09.** — Referenzsuche leer (nur historischer UI-Text), Datei vom Operator entfernt, nicht ersetzt; kein Widerruf am Node | `docs/lightning_macaroon_matrix.md` |
| P0-4 `priority` fehlt bei TV-Alerts | **behoben (gemergt, nicht deployt)** — kein Datenverlust: der Webhook trägt keinen Analysewert; ein Platzhalter hätte `hold_metrics` gefälscht. Anzeige jetzt „Webhook" über `priority_basis` | #999 |

---

## 3. Nicht beurteilbar (kein sudo, keine Node-Shell) — Operator-Befehle

Auf dem Node (`ssh admin@192.168.178.51`, alle lesend):

```bash
# P0-1: gilt das Off-Node-SCB noch?
sha256sum /mnt/hdd/app-data/lnd/data/chain/bitcoin/mainnet/channel.backup      # gegen f9795058a04d0b027850ce60867666cca8a4c6b5bc64181068b64e71df34ef25
stat -c '%y %s' /mnt/hdd/app-data/lnd/data/chain/bitcoin/mainnet/channel.backup
lncli listchannels | jq '.channels[] | {chan_id, capacity, local_balance, remote_balance, active}'
lncli closedchannels | jq '.channels[] | {chan_id, close_type, close_height}'; lncli pendingchannels
# P1-5: tatsaechliche Scopes (nur Ausgabe zitieren, nie die Datei)
lncli printmacaroon --macaroon_file <pfad>/readonly.macaroon
lncli printmacaroon --macaroon_file <pfad>/kai-invoice.macaroon
lncli printmacaroon --macaroon_file <pfad>/kai-payment.macaroon
lncli printmacaroon --macaroon_file <pfad>/kai-cockpit.macaroon
# Forced-Command-Beleg, Versionen, Seed-Backup nur Existenz/Datum
cat /home/admin/.ssh/authorized_keys
lncli version; bitcoin-cli -getinfo
ls -la /home/admin/ | grep -i -E 'seed|mnemonic|backup'
```

Auf dem Pi (sudo, Operator): `ufw status verbose`, `wg show` (wer zieht `wg0` hoch? `wg-quick@wg0` ist inactive), `cat /etc/ssh/sshd_config.d/50-cloud-init.conf`, `journalctl --disk-usage` mit Grenze setzen.

Offen bleibt außerdem: RSS-Langzeitverlauf (Zeitreihe fehlt), Cloudflare-Access-Regelmenge, Inhalt der Backup-Archive (bekannte Lücke), `web/src`-Importgraph, Coverage.

---

## 4. Abarbeitungssprint (Vorschlag, Freigabe erforderlich)

Prinzip: erst Custody und Flut, dann Deploy-Fenster nutzen, dann Code in kleinen PRs, Architektur zuletzt. Alles außer S0 als PR mit Test; S0 sind Operator-Handgriffe ohne Code.

### S0 — Sofort, ohne Code (Operator, heute/morgen, ~1,5 h)
1. **P0-1** Node: SCB-Hash prüfen, bei Abweichung manuell exportieren und off-node sichern (verschlüsselt, zweiter Ort).
2. **P1-2** sshd `PasswordAuthentication no` + fail2ban (sudo).
3. **P1-4** `.env.*`-Backups shredden bis auf 3.
4. **P1-6** `kai-payment.macaroon` vom Pi nehmen, bis das Re-Arm-Runbook es braucht; `kai-cockpit.macaroon` per `printmacaroon` klären (P1-5).
5. **P1-19** `journald` `SystemMaxUse=1G`; `memprobeB` stoppen; nach dem Deploy `/tmp` räumen (124 Dateien) und `.bak`-Units entfernen.
6. **P1-3** Reboot **nach** Deploy und `enable kai-litellm`; vorher Dev-Proxy als Unit oder bewusst offline.

### S1 — Mit dem Deploy-Fenster 17./18.09. (Code, 2 kleine PRs, ~3 h)
1. **P0-2** `useBackendHealth`: stabiles `subscribe`, `emit` nur bei Änderung, Mindestabstand; Test. Danach SPA neu bauen + transferieren. **Entscheidung nötig:** vor dem Deploy einschieben (verschiebt Ziel-SHA, Preflight wiederholen) oder unmittelbar danach als eigenes SPA-Release.
2. **P1-10** `RuntimeMaxSec=120` in `deploy/systemd/kai-entry-watch.service` + **P1-11** `MemoryMax`/`MemoryAccounting` in `kai-server.service` (Unit-Apply ist operator-privilegiert, passt ins Fenster).

### S2 — Codesprint nach dem Deploy (1–2 Wochen, je Punkt eine PR)
1. SCB-Exporter + Timer + Alarm (P0-1), Laptop-Task auf Pi-Pfad. — 1 Tag
2. Kosten-Aggregations-Helper mit Ebenenfilter, alle Leser umstellen (P0-3, P1-13). — 0,5 Tag
3. `priority` für TV-Alerts in den Audit-Record (P0-4). — 0,5 Tag
4. `spendable_offchain_sat` vom Gesamtbestand trennen (P1-8), L402-TTL/Zähler (P2). — 1 Tag
5. entry-watch: Dauerlauf oder oneshot+Timer, Fensterlesen, ein Import (P1-10). — 1 Tag
6. Label-Event für technical_paper-Fills, `message_id`-Sentinel → `null`+`delivery_mode`, pending-Ablauf (P1-14). — 1 Tag
7. Ratchet ab 1 200 LOC (P1-15). — 0,25 Tag
8. `app/agents/registry.py`, `event_hub` nach observability (P1-16). — 0,5 Tag
9. `.env.example` aus `settings.py` generieren + Drift-Test (P1-20); Macaroon-Matrix nachziehen. — 0,5 Tag
10. Unit-Pfade vereinheitlichen, Timer-Provenienz entscheiden (P1-21); Drop-in in Repo. — 0,5 Tag
11. Stille Handler `eligibility.py`, `alerts/service.py`, `daily_briefing.py` → sichtbar (P2). — 0,5 Tag
12. Health-TOCTOU Reihenfolge umdrehen + `last_trigger_utc`-Gurt (P2). — 0,5 Tag
13. `append_lock` auf `bridge_pending_orders.jsonl`, PIPE_BUF-Kommentar korrigieren (P2). — 0,25 Tag
14. `api_request_audit`-Rotation an Rate koppeln, `trading_loop_audit` rotieren, Freshness-Liste ergänzen (P2). — 0,5 Tag
15. Doku: `ARCHITECTURE.md` neu aus dem Baum, Risikoregister (SCB, Kontrast, Light-Theme, Backup-MAC), `git gc`, Branch-Bereinigung. — 0,5 Tag

### S3 — Architektur (nach S2, mit eigener Freigabe)
Hold-/Quality-Pfad mit Fensterlesen und asynchronem Aufbau (P1-11/12); God-File-Schnitte `telegram_bot`, `cli/main`, `dashboard` (P1-17); Orchestrator-Tests (P1-18); `observability`-SPLIT und MERGE truth/integrity/compliance → audit (DEFER seit 02.09.); zweiter Kanal/Peer am Node nach P0-1.

---

## 5. Anhang — Zählungen
Pi: 4 Kerne, 16 GB, Ubuntu 24.04.4, Kernel 6.8.0-1056, Uptime 112 d, Platte 21/117 GB, 63 kai-Units (6 Daemons, 57 Zeitjobs), Journal 4,0 GB, `artifacts/` 1,6 GB (archive 719 MB, backups 175 MB), `dev.db` 530 MB (+3,9 MB/Tag), `logs/` 357 MB. Laptop: Repo 84ce1e26, app 719 py / 177 531 LOC, tests 888 / 192 501 LOC / 8 879 Tests, web 217 / 38 565 LOC, 114 lokale + 49 Remote-Branches, `.git` 193 MB, 23 Scheduled Tasks `KAI-*`. Node: bitcoind 29.2.0 synced (967 273), lnd 0.19.3-beta, 1 Peer, 1 Kanal, 74 d Uptime, Platte 46,8 % von 1,9 TB, Load 1,9, 51 °C.
Rohdaten und Agenten-Kurzfassungen: Session-Scratchpad `audit/` (pi_snapshot.txt, laptop_snapshot.txt, report_*.md); Findings: `artifacts/agents/{watchdog,architect,sentr,satoshi,neo,data-quality-inspector}/findings.jsonl` vom 16.09.

## Nachtrag 16.09. 15:05Z — P0-1 geprüft, keine Abweichung

Prüfweg ohne Node-Shell: lnd-REST-API (`https://10.27.0.51:8080`) vom Pi mit dem **Read-only-Macaroon** (`offchain:read` reicht für `/v1/channels/backup` und `/v1/channels/backup/verify`).

| Beleg | Ergebnis |
|---|---|
| Off-Node-SCB `C:\Users\sasch\channel.backup` (04.07., 756 B, `f9795058…`) | **vom Node verifiziert** (`verify` OK), enthält genau den Kanalpunkt `03147302…:0` |
| Frischer API-Export (757 B, `82965d64…`) | `verify` OK, **derselbe eine Kanalpunkt**; 1 Byte Differenz = neu gepackt (Nonce/Adressliste), nicht ein anderer Kanalsatz |
| Offene Kanäle | 1: `chan_id 1051429984464338944` (Funding-Block 956270 ≈ 02.07., vor dem SCB vom 04.07.) |
| Geschlossene Kanäle | 4, Close-Heights 942189 / 953902 — alle vor dem Funding des offenen Kanals |
| Pending | 0 open / 0 closing / **1 force-closing seit Block 862481** (`cf5fe058…`, Anchor `LIMBO`, 25 815 sat Limbo, `blocks_til_maturity` −104 811) — Altlast, nicht SCB-relevant, aber wert nachzusehen (`lncli pendingchannels`, ggf. Anchor-Sweep) |
| Letzter erfolgreicher Laptop-Pull | **31.08. 22:00** (nicht 15.07.): Node-Hash war da noch `f9795058…`; danach brach die Key-Rotation 31.08. 22:24 den Pfad (`id_ed25519` neu, auf dem Node nicht autorisiert) |

**Verdikt:** Das Off-Node-SCB deckt den aktuellen Kanalsatz vollständig ab; Recovery-Fähigkeit gegeben. Zweite frische Kopie liegt als `KAI-mirror/lightning-scb/channel-20260916-150151.api-export.backup` (Laptop) und `/home/ubuntu/backups/scb/` (Pi, 0600).
**ERLEDIGT 16.09. 18:33Z:** Operator hat den Laptop-Key auf dem Node autorisiert (Eintrag `kai-laptop-to-pi-20260831`). Key-Login geprüft, Live-Datei am Node direkt gelesen: `sha256 f9795058…`, 756 B, mtime 04.07. 16:49 — **byte-identisch mit dem Off-Node-SCB**. `KAI-SCB-Pull-Hourly` manuell gestartet: Exit 0, „current SCB is off-node (plaintext + ciphertext)", Alarm-Marker entfernt. Befehl war:
```
type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh admin@192.168.178.51 "cat >> ~/.ssh/authorized_keys"
```
**S1-Empfehlung angepasst:** SCB-Exporter auf dem Pi über den REST-Weg (Read-only-Macaroon, `/v1/channels/backup` + `verify`) statt SSH-Pull — kein neuer Trust vom Node, läuft ohne Laptop.

**Weitere Node-Reads 16.09. 18:35Z (mit dem neuen Key, alles lesend):**
- `authorized_keys` hat 3 Einträge: `kai-laptop-to-pi` (alter Laptop-Key von vor der Rotation 31.08. — **ENTFERNT 16.09. 18:50Z auf Operator-Freigabe**, Sicherung `authorized_keys.bak-20260916-pre-remove-old-laptop-key` am Node, Login mit neuem Key danach geprüft), `kai-blitz-info` mit Forced Command `python3 /home/admin/kai_blitz_info.py` + no-pty/no-forwarding (Beleg für den Info-Pfad), `kai-laptop-to-pi-20260831` (neu).
- lnd `v0.19.3-beta`, bitcoind Blocks = Headers (967310), Sync 99,9996 %.
- Macaroon-Scopes: `readonly` = nur `*:read`; `kai-invoice` = info/invoices/offchain/onchain read + `invoices:write`; `invoice` (lnd-Standard) zusätzlich `address:write`. `kai-payment`/`kai-cockpit` liegen nicht im Standard-Macaroon-Verzeichnis (P1-5 bleibt: Ablageort und Scopes am Node klären).
- `/home/admin/backups` gehört root (14.06.), keine Seed-/Mnemonic-Datei im Home sichtbar (gut).
- fail2ban auf dem Node **aktiv**; `PasswordAuthentication` nicht explizit gesetzt (= Default ja) → SE-A-002 betrifft weiterhin Passwort-Login.
- Force-Close-Altlast: `cf5fe058…:0`, Closing-Tx `58ae2f35…`, 25 815 sat Limbo, Anchor `LIMBO`, Maturity 862481 (vor ~104 800 Blöcken) — Anchor-Output nie gesweept (Wert < Gebühr); mit `lncli wallet listsweeps`/`pendingsweeps` prüfen, sonst hinnehmen.
- `sudo -n` scheitert stumm (Passwort nötig) — sshd-Härtung bleibt Operator-Handgriff.

## Nachtrag 22.09. — Sprint S2, Stand am Code geprüft, zwei Befunde widerlegt

| Punkt | Stand 22.09. | Beleg |
|---|---|---|
| S2-1 SCB-Exporter + Timer + Alarm | **erledigt** — läuft als `ExecStartPre` in `kai-ln-scb-monitor.service`, Timer + OnFailure vorhanden | #1003 |
| S2-2 Kosten-Ebenenfilter | **erledigt** — `dedupe_chain_levels` in Budget, `/health/ai`, Dashboard; Leseregel in `docs/CODEMAP.md` und `docs/KAI_COST_CONTROL_V0_1.md` | #970, #998 |
| S2-3 `priority` bei TV-Alerts | **erledigt** (`priority_basis`) | #999 |
| S2-11 stille Handler (NEO-A-012/013) | **gemergt** — Dedup-Seed, Briefing „nicht erhebbar", Schwellen-Fallback, `alerts status` | #1029 |
| S2-13 `append_lock` Bridge-Audit (NEO-A-014) | **PR** — einzige Append-Stelle unter Lock (`app/execution/bridge_audit_log.py`), Rotation unter demselben Lock, PIPE_BUF-Doktrin in `file_lock.py` korrigiert | #1030 |
| S2-12 Health-TOCTOU, S2-14 Freshness/Rotation | **erledigt** | #990, #1002 |
| P1-10 `RuntimeMaxSec` entry-watch, P1-11 `MemoryMax` kai-server | **erledigt** | #1005 |
| **DQ-A-010** `message_id="dry_run"` „macht Dedup blind" | **widerlegt** — kein Leser dedupliziert über `message_id` (Dedup läuft über `document_id`, `audit.py::iter_alert_audit_document_ids`); einziger Konsument `web/src/pages/Alerts.tsx::deriveSendStatus` nutzt den Sentinel bewusst. Ein `delivery_mode`-Feld wäre Schema-Hygiene mit Frontend-Anteil, kein Defekt — zurückgestuft | Data-Quality-Inspector 22.09. |
| **DQ-A-007** `trading_loop_audit.jsonl` 95 MB „ohne Rotation" | **widerlegt als Maßnahme** — bewusste HARD EXCLUSION (`repo_hygiene_policy.md`, `CODEMAP.md`, ADR 0003, Test `test_trading_loop_audit_stays_excluded_despite_its_size`): `hold_metrics`, `build_recent_cycles_summary`, `evidence_window` aggregieren den vollen Stream, kein Leser liest `archive/`. Hebel ist der Fenster-Read (#1002). Nebenbefund: `TradingLoop._write_audit` schrieb ohne `append_lock` — jetzt behoben | Neo 22.09. |
| S2-4 `spendable_offchain_sat`, PIPE_BUF-Begründungen in `app/pay`, `app/lightning` | **zurückgestellt** — Codex arbeitet in Pay/Lightning (#1027, `codex/ln-fee-floor-20260922`); Kollisionsgate | — |
| S2-6 Label-Events `technical_paper` | **entfällt vorerst** — `technical_paper` ist abgeschaltet (D-279) | — |
| S2-9 `.env.example`-Drift | **erledigt** — Drift-Ratchet gegen die Settings-Klassen (39 Klassen), Lücke `TRADINGVIEW_WEBHOOK_SHARED_TOKEN` geschlossen; Nachtrag S2-9b: Vorlage lädt wieder (`APP_CORS_ALLOWED_ORIGINS` war als `list[str]` kommagetrennt), 18 Sicherheits-Schlüssel dokumentiert, Baseline 409 → 388 | #1033, #1038 |
| **S2-8 Teil 1** `event_hub` → `app/observability/` (AR-A-003) | **erledigt 23.09.** — Modul verschoben, alle drei Publisher/Subscriber umgestellt, kein Shim: die beiden Aufwärtskanten aus `app.alerts`/`app.execution` waren genau der Grund für die lazy, exception-geschluckten Imports und sind jetzt gewöhnliche Top-Level-Importe (`paper_engine.py` 1882 → 1881) | S2-8 PR-1 |
| **S2-8 Teil 2** Agenten-SSOT `_AGENTS` → `app/agents/registry.py` (AR-A-002) | **offen, nach dem Deploy-HOLD** — berührt `kai-agent-worker` und `kai-tg-listener` als eigene Units sowie das Drei-Register-Kontrakt (`_AGENTS`, `CLAUDE.md`, `.claude/agents/*.md`); `telegram_bot.py` steht bei 3339/3339, der Import muss aus EINEM Modul kommen | — |
| S2-10 Unit-Pfade | **offen** — Operator-Entscheid (Timer aus dem Checkout vs. `current/`) | — |

