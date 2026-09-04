# Runbook — Payment Fabric (ADR 0018)

Für den Operator am Gerät. Drei Modi, ein Journal, ein sendender Prozess.

| Modus | Node-Kontakt | Sendet |
|---|---|---|
| `simulation` (Default) | keiner | nein (deterministischer `SimulationRail`) |
| `shadow` | read-only (Decode, Health, Quote) | **nie** — `execute` verweigert |
| `live` | voll | ja, nur mit allen vier Vorbedingungen |

Alle Aufrufe brauchen den Bearer. **Nie** einen Tokenwert in ein Protokoll,
Ticket oder eine Sitzungsaufzeichnung schreiben:

```bash
export KAI="Authorization: Bearer $APP_API_KEY"   # Wert kommt aus der .env, nicht aus der Historie
export BASE=http://127.0.0.1:8000
```

---

## 1. SIMULATION-Smoke (jederzeit, kein Node)

```bash
curl -sS -H "$KAI" "$BASE/health/payment" | jq '{status, mode, journal, rail}'
# erwartet: status "ok", mode "simulation", journal.chain "ok", rail.state "simulated"

curl -sS -X POST -H "$KAI" -H 'Content-Type: application/json' \
     -H "Idempotency-Key: $(uuidgen)" \
     -d '{"actor":"operator","purpose":"self_test","destination":"sim:settle:smoke",
          "amount_sat":100,"fee_limit_sat":5}' \
     "$BASE/payments/intents" | jq '{intent_id, status, verdict, rule_ids}'
```

`status` ist **`DENIED` mit `rule_ids:["destination_allowlist"]`**, solange
`APP_PAYMENT_DESTINATION_ALLOWLIST` leer ist. Das ist der korrekte
Auslieferungszustand — eine leere Allowlist ist eine geschlossene Tür. Für den
Smoke den SHA-256 des Payee-Hashs aus der Antwort eintragen, Server neu
starten, Aufruf wiederholen.

```bash
INTENT=pi_...
curl -sS -X POST -H "$KAI" "$BASE/payments/intents/$INTENT/simulate" | jq .quote
curl -sS -X POST -H "$KAI" -H 'Content-Type: application/json' \
     -d '{"hotp_code":""}' "$BASE/payments/intents/$INTENT/execute" | jq '{status, replayed}'
curl -sS -H "$KAI" "$BASE/payments/audit?intent_id=$INTENT" | jq '.events[].event_type'
```

Der zweite `execute`-Aufruf antwortet `replayed: true` und sendet **nicht**.

**Self-Use-Receivable:**

```bash
curl -sS -X POST -H "$KAI" -H 'Content-Type: application/json' \
     -d '{"amount_sat":1000,"purpose":"self_test","order_ref":"smoke-1",
          "expiry_seconds":3600,"memo":"kai-pay: self_test"}' \
     "$BASE/payments/invoices" | jq '{ref_hash, order_ref, expires_at, payment_request}'
curl -sS -H "$KAI" "$BASE/payments/invoices/<ref_hash>" | jq '{settled, amount_paid_minor_units}'
```

`payment_request` ist die BOLT11 — **sie** bekommt der Zahler, nicht der
`ref_hash`. Sie steht bewusst nur in dieser Antwort: zu einer Forderung trägt
das Journal ausschließlich `invoice_ref_hash` (Allowlist in
`app/payments/redaction.py`).

`memo` ist der Text, den der **Node** in die Invoice schreibt. Leer heißt nicht
„kein Memo", sondern `kai-pay: <purpose>` — die Einnahmenerkennung
(`app/lightning/earnings_ledger.py`) findet eigene Invoices an genau diesem
Präfix, und ohne es bucht sie den Eingang nicht (Befund 2026-09-04). Ins Journal
geht nur `memo_hash`.

`expiry_seconds` ist per Default **3600**, Obergrenze **86400**. 300 s reichen
einem Menschen nicht — Wallet öffnen, scannen, bestätigen dauert länger, als
der QR-Code lebte (Rückweg-Test 2026-09-04).

---

## 2. SHADOW-Preview (read-only am echten Node)

```
APP_PAYMENT_MODE=shadow
APP_LN_ENABLED=true
APP_LN_TLS_CERT_PATH=/…/tls.cert
APP_LN_MACAROON_PATH=/…/readonly.macaroon      # Read-Scope
APP_LN_PAY_ENABLED=false                        # bleibt aus
APP_PAYMENT_VAULT_KEY=<openssl rand -base64 32>
```

`APP_PAYMENT_VAULT_KEY` ist ab SHADOW **Pflicht** — der Server startet sonst
nicht. Grund: SHADOW legt bereits Intents an, und ein Intent, der zwischen
Anlage und Freigabe einen Neustart nicht überlebt, ist genau der Befund vom
2026-09-04. Der Schlüssel muss da sein, **bevor** der erste Vorgang entsteht,
nicht erst wenn Geld fließt. In SIMULATION greift ein abgeleiteter
Test-Schlüssel; ihn produktiv einzutragen verweigert der Boot-Guard.

Nach `systemctl restart kai-server`:

```bash
curl -sS -H "$KAI" "$BASE/health/payment" | jq '.rail'
# reachable/synced_to_chain/synced_to_graph/wallet_locked — ohne Pfade, ohne Credentials
```

Intent anlegen und `simulate` aufrufen: Decode, Allowlist-Bindung und
Fee-Schätzung laufen gegen den echten Node. `execute` antwortet **409** mit
„payment mode is shadow — it reads and computes, it never sends“.

`estimate_source` sagt, woher die Gebührenschätzung stammt (`settings_ppm` oder
`node_estimate_route_fee`). Eine Schätzung ohne genannte Herkunft wird später
für eine Messung gehalten — deshalb steht sie immer dabei.

---

## 3. HOTP-Provisionierung (Vorbedingung für jedes LIVE-Fenster)

Im LIVE-Fenster am 2026-09-04 scheiterten **drei** Freigaben hintereinander,
bevor eine klappte. Die Ursache war nicht der Code, sondern die
Provisionierung: der Eintrag im YubiKey trug ein anderes Secret als der Seed auf
dem Pi. An der API sieht beides gleich aus (`approval refused`) — deshalb ist
die Provisionierung eine Vorbedingung mit eigenem Abschnitt und nicht ein Satz
in der Ablaufliste.

Diese Schritte **vor** dem Fenster, nicht darin.

```bash
# 1. Seed erzeugen (überschreibt nur mit --force; der alte Seed ist danach weg)
.venv/bin/python scripts/hotp_provision.py --force

# 2. Zähler-Journal explizit initialisieren — es wird NIE implizit angelegt
.venv/bin/python scripts/hotp_bootstrap.py --next-counter 0

# 3. Lokale Gegenprobe: welchen Zähler erwartet der Pi?
.venv/bin/python scripts/hotp_check_counter.py
# erwartet: last_used_counter=-1 next_expected_counter=0
```

`hotp_check_counter.py` gibt **nur** die Zählerposition aus, nie den Seed. Es
verbraucht keinen Code und bewegt nichts; es ist in jeder Lage sicher.

Schritt 1 gibt Seed und `otpauth://`-URI aus. Beides gehört in den
Authenticator und **nirgendwo sonst hin** — kein Ticket, kein Chat, keine
Sitzungsaufzeichnung.

**Eintrag im YubiKey Authenticator / Aegis / FreeOTP:**

| Feld | Wert |
|---|---|
| Typ | **HOTP** (zählerbasiert), nicht TOTP |
| Algorithmus | SHA1 |
| Stellen | 6 |
| Zähler | **0** |

Nach jedem Seed-Wechsel:

1. Den **alten Eintrag im Authenticator löschen.** Zwei Einträge mit demselben
   Namen und verschiedenen Secrets sind der Fehler vom 2026-09-04 — sichtbar
   erst an drei Fehlversuchen.
2. `systemctl restart kai-server` — der Verifier liest den Seed beim Bauen des
   Dienstes, ein laufender Prozess kennt den neuen nicht.
3. **Den Intent NEU anlegen.** Ein Intent, der vor dem Seed-Wechsel entstanden
   ist, wartet auf eine Freigabe aus der alten Zeremonie.

Sitzt der Zähler auseinander (Authenticator weiter als der Pi), zeigt
`hotp_check_counter.py` das sofort: `next_expected_counter` ist die Wahrheit des
Pi. Der Verifier akzeptiert ein kleines Vorlauffenster; liegt der Authenticator
weiter, hilft nur ein neuer Bootstrap mit belegter Position — nie ein Raten.

---

## 4. LIVE-Testfenster (nur mit ausdrücklichem Operator-Go)

### Vorbedingungen (alle vier, sonst startet der Server nicht)

`validate_payment_boot` bricht den Start ab, wenn eine fehlt:

1. `APP_ENV=production`
2. `APP_LN_PAY_ENABLED=true` (der verdrahtete Kill-Switch bleibt das äußerste Tor)
3. `APP_LN_PAYMENT_MACAROON_PATH` zeigt auf eine **Datei** (kein Hex im Environment: das trägt kein `0600` und überlebt in jeder Prozessliste und jedem Crash-Dump)
4. `APP_LN_HOTP_SEED_PATH` vorhanden, `APP_PAYMENT_FEE_LIMIT_DEFAULT_PPM > 0`

Zusätzlich, **in jedem Modus**: `APP_LN_MACAROON_PATH` und
`APP_LN_INVOICE_MACAROON_PATH` dürfen nicht auf dieselbe Datei zeigen. Sonst
trägt jeder Lesepfad Invoice-Schreibrechte — eine Rechteausweitung, die man nur
an zwei Env-Zeilen sieht.

### Konfiguration für das Fenster

```
APP_ENV=production
APP_PAYMENT_MODE=live
APP_LN_PAY_ENABLED=true
APP_LN_MACAROON_PATH=/…/readonly.macaroon
APP_LN_PAYMENT_MACAROON_PATH=/…/payment.macaroon
APP_LN_HOTP_SEED_PATH=/…/hotp_seed.b32
APP_PAYMENT_VAULT_KEY=<openssl rand -base64 32>
APP_PAYMENT_PER_PAYMENT_MAX_SAT=1000
APP_PAYMENT_DAILY_HARD_CAP_SAT=1000
APP_PAYMENT_FEE_LIMIT_MAX_SAT=5
APP_PAYMENT_APPROVAL_THRESHOLD_SAT=1          # jede Zahlung braucht HOTP
APP_PAYMENT_DESTINATION_ALLOWLIST=<sha256(payee_pubkey)>
APP_PAYMENT_PURPOSES_ALLOWED=self_test
```

Betrag: **1.000 sat**, Ziel: eine EIGENE Invoice (Self-Payment). Ein fremdes
Ziel im ersten Fenster verschenkt den einzigen Vorteil des Testes — die
Rückholbarkeit.

### Ablauf

```bash
# 0. Ausgangslage festhalten
curl -sS -H "$KAI" "$BASE/health/payment" | jq '{status, mode, journal, live_gate}'

# 1. Intent
curl -sS -X POST -H "$KAI" -H 'Content-Type: application/json' \
     -H "Idempotency-Key: $(uuidgen)" \
     -d '{"actor":"operator","purpose":"self_test","destination":"<BOLT11>",
          "amount_sat":1000,"fee_limit_sat":5}' "$BASE/payments/intents"
# erwartet: status AWAITING_APPROVAL, rule_ids ["approval_threshold"]

# 2. Vorschau — Route und Gebühr, ohne Send
curl -sS -X POST -H "$KAI" "$BASE/payments/intents/$INTENT/simulate" | jq .quote

# 3. Freigabe + Send (HOTP aus dem Authenticator)
curl -sS -X POST -H "$KAI" -H 'Content-Type: application/json' \
     -d '{"hotp_code":"123456"}' "$BASE/payments/intents/$INTENT/execute"

# 4. Zustand + Beleg
curl -sS -H "$KAI" "$BASE/health/payment" | jq '{status, last_settlement, in_flight, fees_minor_units}'
curl -sS -H "$KAI" "$BASE/payments/audit?intent_id=$INTENT" | jq '.events[].event_type'

# 5. Reconciliation von Hand anstoßen
sudo systemctl start kai-ln-reconcile.service
jq . artifacts/payments/reconcile_state.json
```

**Der Intent überlebt jetzt einen Neustart.** Seit v0.2 liegen die Rohfelder
eines freigegebenen, noch nicht gesendeten Vorgangs verschlüsselt in
`artifacts/payments/intent_vault.jsonl` (AES-256-GCM, `0600`). Nach
`systemctl restart kai-server` ist derselbe `intent_id` weiter ausführbar — das
Neuanlegen mitten im Fenster entfällt. Zurückgeholt werden **nur**
`AWAITING_APPROVAL` und `AUTHORIZED`; alles ab `SUBMITTED` geht weiter über die
Reconciliation, und das bleibt so.

**Die Gegen-Wallet braucht Guthaben für die Routing-Gebühr.** Am 2026-09-04
blieben zwei Invoices unbezahlt, weil das Wallet auf der anderen Seite den
Betrag noch decken konnte, die Gebühr aber nicht mehr. Das sieht aus wie ein
Fehler bei KAI und ist keiner: die Invoice läuft einfach ab. Vor dem Rückweg-Test
also prüfen, dass drüben Betrag **plus** Gebühr verfügbar sind.

**Die Paste-Falle: URL zuerst.** Am 2026-09-04 landete der Betriebs-API-Key im
Terminal-Paste des Operators und musste rotiert werden. Der Grund ist die
Reihenfolge: wer `curl -H "$KAI" …` aus der Historie holt und die URL nachträgt,
hat den Header schon auf dem Schirm. Deshalb **erst die URL** in die Zeile,
Header und Body danach — und Zugangsdaten immer nur als `$VAR`, nie als Wert.

**Wenn `execute` in `RECONCILIATION_REQUIRED` endet, ist das kein Fehler,
sondern die richtige Antwort.** Sie heißt: der Node hat nicht geantwortet, und
niemand weiß, ob Geld geflossen ist. **Nicht wiederholen.** Den Reconcile-Lauf
abwarten — er holt die Evidenz und macht `SETTLED` oder `FAILED_FINAL` daraus.
Ein Retry an dieser Stelle ist der 25k-Spend vom 07-02.

### Rückbau (unmittelbar nach dem Fenster)

```
APP_LN_PAY_ENABLED=false
APP_PAYMENT_MODE=simulation
```

`systemctl restart kai-server`, danach `GET /health/payment` prüfen:
`mode` = `simulation`, `live_gate.pay_enabled` = `false`. Erst dann ist das
Fenster geschlossen.

---

## 5. Rollback

| Lage | Schritt |
|---|---|
| Zahlung hängt in `RECONCILIATION_REQUIRED` | `kai-ln-reconcile.service` starten, `reconcile_state.json` lesen. Nicht senden. |
| Journal-Kette gebrochen | Server **nicht** neu starten (er verweigert ohnehin den Boot). Journal sichern, `python -c "from app.payments.journal import PaymentJournal; print(PaymentJournal().verify_chain())"`, Backup ziehen, ADR 0018 §5 folgen. |
| Waisen-Settlement gemeldet | `artifacts/payments/payment_journal.jsonl` nach `orphan_settlement` durchsuchen, `rail_dedup_key` am Node nachschlagen. Bei Erstinbetriebnahme ist eine einmalige Meldung der Alt-Historie erwartbar (`window_enforced=false`). |
| Modus versehentlich `live` | `APP_LN_PAY_ENABLED=false` genügt: der Kill-Switch sitzt ausserhalb des Modus, `LightningRail.pay` verweigert. Danach Modus zurückstellen. |
| Control Plane nicht verdrahtet | `/health/payment` meldet `degraded` mit Grund, `/payments/*` antwortet 503. Server-Log auf `validate_payment_boot` prüfen. |
| Server startet nicht: „payment vault line N cannot be opened" | `APP_PAYMENT_VAULT_KEY` passt nicht zu `artifacts/payments/intent_vault.jsonl`. Erst den richtigen Schlüssel suchen (`/health/config` zeigt den Fingerprint des geladenen). Ist er wirklich verloren: **prüfen, dass kein Vorgang offen auf Freigabe wartet** (`GET /payments/audit`), dann die Vault-Datei beiseitelegen und die betroffenen Intents neu anlegen. Das Journal bleibt unangetastet — es ist die Wahrheit, der Vault nur das Material. |
| HOTP-Freigabe wird abgelehnt | Nicht den Code wiederholen. `scripts/hotp_check_counter.py` und § 3 — meist ist es die Provisionierung, nicht der Code. |

Der Reconcile-Timer **sendet nie**. Ihn zu starten ist in jeder Lage sicher.

---

## 6. Alarmpfade

| Komponente | Klasse | Auslöser |
|---|---|---|
| `payment_journal` | P0 | Kette gebrochen oder unlesbar (`_check_payment_journal_chain`) |
| `payment_intent_vault` | P1 | Vault-Zeile unlesbar, oder ein freigabebereiter Vorgang hat keinen Eintrag und überlebt den nächsten Neustart nicht (derselbe Wächter) |
| `payment_reconciliation` | P0 | letzter Lauf `attention`: Waise, ungeklärter Send, Uhr-Sprung **oder Doppelbefund beider Geldjournale** (`_check_payment_reconciliation`) |

`dual_journal_conflict` heißt: eine Zahlung steht in **beiden** Büchern
(`ln_ops_ledger_v2.jsonl` und `payment_journal.jsonl`), und der Altpfad hat sie
nicht bewiesen abgeschlossen. Der `rail_dedup_key` im Record ist der
`payment_hash` — damit lässt sich die Zeile im v2-Journal finden. **Nicht
erneut senden**: erst am Node nachschlagen, dann den offenen v2-Intent
abschließen. Mit dem Rückbau des Altpfads (ADR 0018 § 12) verschwindet diese
Klasse.

Beide laufen im bestehenden Health-Check-Pfad und gehen damit über den
regulären Telegram-Kanal — nicht nur über `OnFailure=` der Unit. Der
Unterschied ist wesentlich: ein Reconcile-Lauf mit Befund ist ein
**erfolgreicher** Lauf, `OnFailure=` sähe ihn nie.

Überwacht wird die Kette, nicht die Kadenz. Der Strom ist ereignisgetrieben und
in SIMULATION legitim tagelang still; eine Freshness-Schwelle wäre entweder
wirkungslos oder ein Daueralarm — und eine Wache, die immer schreit, wird
abgeschaltet.

## 7. Backup

`payment_journal.jsonl`, `intent_vault.jsonl`, `ln_ops_ledger_v2.jsonl` und
`ln_hotp_journal.jsonl` liegen in `kai_backup_artifacts.sh::DEFAULT_SOURCES`. Fehlt eines davon,
obwohl es im Manifest des letzten Archivs stand, bricht das Backup mit
`fail_missing_money_journal` ab — geprüft wird gegen Evidenz, nicht gegen eine
Behauptung. Auf einer Anlage, die noch nie gezahlt hat, ist ihr Fehlen normal
und kein Fehler.

Der **Schlüssel** zum Vault (`APP_PAYMENT_VAULT_KEY`) liegt in der `.env` und
gehört ausdrücklich **nicht** in dieses Archiv: ein Backup, das Chiffrat und
Schlüssel zusammen trägt, ist kein verschlüsseltes Backup. Wer den Vault
zurückspielt, braucht beides — aus zwei verschiedenen Quellen.
