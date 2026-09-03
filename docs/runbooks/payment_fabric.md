# Runbook — Payment Fabric (ADR 0017)

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
     -d '{"amount_sat":1000,"purpose":"self_test","order_ref":"smoke-1"}' \
     "$BASE/payments/invoices" | jq '{ref_hash, order_ref}'
curl -sS -H "$KAI" "$BASE/payments/invoices/<ref_hash>" | jq '{settled, amount_paid_minor_units}'
```

---

## 2. SHADOW-Preview (read-only am echten Node)

```
APP_PAYMENT_MODE=shadow
APP_LN_ENABLED=true
APP_LN_TLS_CERT_PATH=/…/tls.cert
APP_LN_MACAROON_PATH=/…/readonly.macaroon      # Read-Scope
APP_LN_PAY_ENABLED=false                        # bleibt aus
```

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

## 3. LIVE-Testfenster (nur mit ausdrücklichem Operator-Go)

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

## 4. Rollback

| Lage | Schritt |
|---|---|
| Zahlung hängt in `RECONCILIATION_REQUIRED` | `kai-ln-reconcile.service` starten, `reconcile_state.json` lesen. Nicht senden. |
| Journal-Kette gebrochen | Server **nicht** neu starten (er verweigert ohnehin den Boot). Journal sichern, `python -c "from app.payments.journal import PaymentJournal; print(PaymentJournal().verify_chain())"`, Backup ziehen, ADR 0017 §5 folgen. |
| Waisen-Settlement gemeldet | `artifacts/payments/payment_journal.jsonl` nach `orphan_settlement` durchsuchen, `rail_dedup_key` am Node nachschlagen. Bei Erstinbetriebnahme ist eine einmalige Meldung der Alt-Historie erwartbar (`window_enforced=false`). |
| Modus versehentlich `live` | `APP_LN_PAY_ENABLED=false` genügt: der Kill-Switch sitzt ausserhalb des Modus, `LightningRail.pay` verweigert. Danach Modus zurückstellen. |
| Control Plane nicht verdrahtet | `/health/payment` meldet `degraded` mit Grund, `/payments/*` antwortet 503. Server-Log auf `validate_payment_boot` prüfen. |

Der Reconcile-Timer **sendet nie**. Ihn zu starten ist in jeder Lage sicher.

---

## 5. Alarmpfade

| Komponente | Klasse | Auslöser |
|---|---|---|
| `payment_journal` | P0 | Kette gebrochen oder unlesbar (`_check_payment_journal_chain`) |
| `payment_reconciliation` | P0 | letzter Lauf `attention`: Waise, ungeklärter Send oder Uhr-Sprung (`_check_payment_reconciliation`) |

Beide laufen im bestehenden Health-Check-Pfad und gehen damit über den
regulären Telegram-Kanal — nicht nur über `OnFailure=` der Unit. Der
Unterschied ist wesentlich: ein Reconcile-Lauf mit Befund ist ein
**erfolgreicher** Lauf, `OnFailure=` sähe ihn nie.

Überwacht wird die Kette, nicht die Kadenz. Der Strom ist ereignisgetrieben und
in SIMULATION legitim tagelang still; eine Freshness-Schwelle wäre entweder
wirkungslos oder ein Daueralarm — und eine Wache, die immer schreit, wird
abgeschaltet.

## 6. Backup

`payment_journal.jsonl`, `ln_ops_ledger_v2.jsonl` und `ln_hotp_journal.jsonl`
liegen in `kai_backup_artifacts.sh::DEFAULT_SOURCES`. Fehlt eines davon,
obwohl es im Manifest des letzten Archivs stand, bricht das Backup mit
`fail_missing_money_journal` ab — geprüft wird gegen Evidenz, nicht gegen eine
Behauptung. Auf einer Anlage, die noch nie gezahlt hat, ist ihr Fehlen normal
und kein Fehler.
