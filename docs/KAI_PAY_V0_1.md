# KAI PAY v0.1 — Zahlung anfordern, Eingang automatisch bestätigen

**Entscheidung:** D-CORE-006 · **Kern:** [ADR 0018](adr/0018-payment-fabric-control-plane.md) (versiegelt, D-CORE-005) · **Stand:** 2026-09-08

## 1. Was das ist — und was nicht

Ein Mensch oder ein Dienst fordert eine Zahlung an, bezahlt per Lightning, und KAI bestätigt
den Eingang. Mehr nicht.

**Kein** Benutzerkonto, **kein** Shop, **kein** Warenkorb, **kein** zweiter Rail, **kein**
L402-Ausbau. Der Payment Control Plane (`app/payments/`) bleibt versiegelt und wird nur
gewartet; `app/pay/` ist eine dünne Produktschicht **darüber**.

**Die eine Regel, an der alles hängt:** Geldwahrheit — bezahlt / wie viel / wann — kommt
ausschließlich aus dem Kern. Die Produktschicht speichert Präsentations- und
Verknüpfungsdaten, nie einen Betrag als Wahrheit.

| Frage | Wer antwortet |
|---|---|
| Wie lautet die Forderung? | `PaymentService.create_invoice` (Rail + Journal-Record) |
| Wurde bezahlt? | `app/payments/receivables.py::settle_receivable` |
| Wie viel, wann, nachweisbar? | `app/payments/receivables.py::settlement_of` (Journal-Record) |
| Wann läuft sie ab? | `app/payments/receivables.py::expiry_of` (Journal-Record) |

`settle_receivable` ist derselbe Durchgang, den `kai-ln-reconcile.timer` alle 15 Minuten
fährt. Es gibt **genau einen** Schreiber von `receivable_settled` im ganzen Repo
(`tests/unit/pay/test_pay_never_writes_money.py` erzwingt das per AST). Der Poller ist
deshalb kein zweiter Reconciler, sondern ein zweiter **Takt** auf demselben Durchgang.

## 2. Statussprache

Nach außen genau vier Worte:

| Status | Bedeutung | Woher |
|---|---|---|
| `WAITING` | Forderung steht, niemand hat bezahlt | Default |
| `SETTLED` | Eingang im Geld-Journal gebucht — **final** | `receivable_settled`-Record |
| `EXPIRED` | Rail sagt „nicht bezahlt" UND die Frist der Invoice ist um | Rail + Journal-`expires_at` |
| `FAILED` | endgültige Absage des Rails | heute liefert das kein Rail |

Drei Regeln mit Preis auf der Gegenseite:

1. **`SETTLED` ist final.** Nie ein Rückweg — eine zweimal gelieferte Leistung ist teurer
   als eine späte Anzeige.
2. **`EXPIRED` braucht eine Aussage des Rails.** Es ist eine Behauptung über Geld
   („niemand hat gezahlt"), und die darf die Uhr allein nicht treffen. Antwortet der Rail
   nicht, bleibt es `WAITING` (fail-soft, Grund in `last_error`).
3. **`FAILED` nur bei endgültiger Rail-Aussage.** `InvoiceStatus` kennt heute nur
   `settled` ja/nein; die Abbildung steht trotzdem, damit ein späterer Rail mit
   Storno-Zustand genau eine Landestelle hat.

## 3. API

Alle Endpunkte liegen hinter derselben Bearer-/CF-Access-Grenze wie `/payments/*`.
Ohne `APP_PAY_ENABLED=true` antwortet jeder mit `404 {"detail": "kai pay disabled"}`.

### Zahlung anfordern

```bash
curl -sS -X POST https://kai-trader.org/pay/requests \
  -H "Authorization: Bearer $APP_API_KEY" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: order-4711-attempt-1" \
  -d '{
        "amount_sat": 2500,
        "description": "Beratung 30 Minuten",
        "reference": "ORDER-4711",
        "expiry_seconds": 900,
        "webhook_url": "https://shop.example/kai-pay"
      }'
```

```json
{
  "payment_id": "pay_9f2c41ab77de",
  "status": "WAITING",
  "amount_sat": 2500,
  "description": "Beratung 30 Minuten",
  "reference": "ORDER-4711",
  "bolt11": "lnbc25u1p...",
  "lightning_uri": "lightning:lnbc25u1p...",
  "created_at": "2026-09-08T12:00:00+00:00",
  "expires_at": "2026-09-08T12:15:00+00:00"
}
```

`Idempotency-Key` ist **optional** (anders als bei `/payments/intents`, wo er Pflicht ist):
dort würde ein Retry zu einem zweiten SEND, hier nur zu einem zweiten QR-Code. Wer den
Header schickt, bekommt bei jedem Retry dieselbe Antwort — auch über einen Neustart hinweg.

`amount_sat` 1…`APP_PAY_MAX_AMOUNT_SAT` · `description` 1…140 · `reference` ≤ 64 ·
`expiry_seconds` 60…86400 · `webhook_url` muss `https` sein.

### Stand abfragen

```bash
curl -sS https://kai-trader.org/pay/requests/pay_9f2c41ab77de \
  -H "Authorization: Bearer $APP_API_KEY"
```

```json
{
  "payment_id": "pay_9f2c41ab77de",
  "status": "SETTLED",
  "amount_sat": 2500,
  "paid_amount_sat": 2500,
  "paid_at": "2026-09-08T12:03:41+00:00",
  "reference": "ORDER-4711",
  "description": "Beratung 30 Minuten",
  "created_at": "2026-09-08T12:00:00+00:00",
  "expires_at": "2026-09-08T12:15:00+00:00",
  "bolt11": null,
  "lightning_uri": null,
  "last_error": ""
}
```

Der Aufruf fragt **frisch beim Kern nach** (`settle_receivable`) und bucht dort, wenn
bezahlt wurde. `bolt11`/`lightning_uri` stehen nur bei `WAITING` in der Antwort — damit
die Seite nach einem Reload denselben QR zeigen kann, ohne eine zweite Forderung
auszustellen; nach dem Eingang wären sie eine Einladung, zweimal zu zahlen.

### Nach Bestellreferenz suchen

```bash
curl -sS "https://kai-trader.org/pay/requests?reference=ORDER-4711" \
  -H "Authorization: Bearer $APP_API_KEY"
# {"reference":"ORDER-4711","paid":true,"payment_ids":["pay_9f2c41ab77de"],"latest":{…}}

curl -sS "https://kai-trader.org/pay/requests?limit=10" \
  -H "Authorization: Bearer $APP_API_KEY"
# {"requests":[…]}  — neueste zuerst, limit 1…100
```

### Beleg

```bash
curl -sS https://kai-trader.org/pay/requests/pay_9f2c41ab77de/receipt \
  -H "Authorization: Bearer $APP_API_KEY"
curl -sS "https://kai-trader.org/pay/requests/pay_9f2c41ab77de/receipt?format=text" \
  -H "Authorization: Bearer $APP_API_KEY"
```

```json
{
  "receipt_id": "rcpt_3a91c0f48b2d",
  "payment_id": "pay_9f2c41ab77de",
  "amount_sat": 2500,
  "paid_amount_sat": 2500,
  "paid_at": "2026-09-08T12:03:41+00:00",
  "reference": "ORDER-4711",
  "description": "Beratung 30 Minuten",
  "rail": "lightning",
  "audit": {"journal_seq": 184, "record_hash": "3a91c0f48b2d…"},
  "created_at": "2026-09-08T12:00:00+00:00"
}
```

`receipt_id` ist aus dem `record_hash` abgeleitet: derselbe Eingang ergibt immer denselben
Beleg. `audit` zeigt auf den Record in `artifacts/payments/payment_journal.jsonl` — dort
lässt sich jede Zahl nachrechnen. Ohne gebuchten Eingang: `404`.

### Betriebszustand

```bash
curl -sS https://kai-trader.org/pay/health -H "Authorization: Bearer $APP_API_KEY"
# {"enabled":true,"open_requests":3,"settled_total":11,"last_settled_at":"…","poller_alive":true}
```

## 4. Webhook

Bei Eingang **genau einmal** ein `POST` an `webhook_url`:

```json
{"payment_id":"pay_…","status":"SETTLED","amount_sat":2500,"paid_amount_sat":2500,
 "paid_at":"…","reference":"ORDER-4711","description":"…","ts":"…"}
```

Header: `X-KAI-Pay-Signature: sha256=<HMAC-SHA256(APP_PAY_WEBHOOK_SECRET, body)>` und
`X-KAI-Pay-Timestamp`. Timeout 10 s, 3 Versuche mit 1 s/2 s Pause. **Ohne
`APP_PAY_WEBHOOK_SECRET` wird nichts gesendet** (Store-Event `webhook_failed`,
Grund `no_secret`): ein unsignierter Callback ist eine Nachricht, die jeder fälschen kann,
der die URL kennt.

Der Callback blockiert nie einen Zustand. Bezahlt ist, was das Journal sagt — nicht, was
ein HTTP-Aufruf sagt.

### Verifikation beim Empfänger (5 Zeilen)

```python
import hashlib, hmac

def verify(body: bytes, header: str, secret: str) -> bool:
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)  # NIE ==, sonst Timing-Leck
```

Über die **rohen Bytes** des Requests, nicht über ein neu serialisiertes Dict — zwei
Serialisierungen sind die klassische Signaturlücke.

## 5. Konfiguration (Pi)

```ini
# /etc/kai/kai.env  (o. ä. — dieselbe Datei wie APP_PAYMENT_*)
APP_PAY_ENABLED=true
APP_PAY_PURPOSE=kai_pay
APP_PAY_DEFAULT_EXPIRY_SECONDS=900
APP_PAY_MAX_AMOUNT_SAT=1000000
APP_PAY_POLL_INTERVAL_SECONDS=20
APP_PAY_MAX_OPEN_REQUESTS=200
APP_PAY_WEBHOOK_SECRET=<openssl rand -hex 32>

# ZWINGEND: die Allowlist des Kerns muss den Zweck kennen.
APP_PAYMENT_PURPOSES_ALLOWED=data_subscription,api_credit,self_test,kai_pay
```

**Der Startguard ist scharf.** Steht `kai_pay` nicht in `APP_PAYMENT_PURPOSES_ALLOWED`,
startet `kai-server` **nicht** (`ConfigurationError`). Das ist Absicht: sonst würde jede
Forderung erst *nach* ihrem Journal-Record an der Policy scheitern.

Reihenfolge der Guards beim Boot:
`validate_lightning_boot` → `validate_payment_boot` → `validate_pay_boot`.

Der Rail folgt weiterhin `APP_PAYMENT_MODE`: In `simulation` bewegt sich kein echtes Geld,
in `shadow`/`live` spricht der Empfangspfad mit dem Node. Ein echter Eingang braucht
`APP_PAYMENT_MODE=shadow` oder `live` — **`APP_LN_PAY_ENABLED` bleibt davon unberührt**,
denn KAI PAY empfängt nur und sendet nie.

## 6. Artefakt

`artifacts/pay/requests.jsonl` — append-only, ein Prozess (`kai-server`), ohne Lock und
ohne Hash-Kette. Events: `request_created` · `settled_observed` · `expired_observed` ·
`failed_observed` · `webhook_sent` · `webhook_failed`.

Er trägt **keine Geldwahrheit**: Statuswort für die Anzeige, `payment_id ↔ ref_hash`,
Beschreibung, Referenz, Callback-Ziel, BOLT11 und ein Zeiger (`journal_seq`,
`record_hash`) auf den Record, der Betrag und Zeitpunkt beweisbar hält. Ein verlorener
Eintrag kostet eine Anzeige, nie einen Satoshi.

Bewacht von `_check_pay_requests` (`app/alerts/health_check_pay.py`) — Form, nicht Kadenz:
der Strom ist ereignisgetrieben und im Default-Zustand gar nicht vorhanden. Vertrag in
`config/stream_contracts.json` (`monitoring: alternative_watcher`).

**Nicht** in `kai_backup_artifacts.sh::MONEY_SOURCES`: dort steht, was Geld beweist. Die
Zuordnung ist aus dem Geld-Journal rekonstruierbar (jeder Receivable-Record trägt
`invoice_ref_hash` und `order_ref`).

## 7. Abnahme am Gerät

1. `APP_PAY_ENABLED=true`, Allowlist ergänzt, `kai-server` neu gestartet
   (danach `agent-worker` + `tg-listener` + `cloudflared` smoke-prüfen — Restart-Protokoll).
2. Browser → Betrag + Beschreibung → QR.
3. Echte Zahlung aus einer Wallet.
4. Seite zeigt binnen ≤ `APP_PAY_POLL_INTERVAL_SECONDS` `SETTLED`.
5. Gegenprobe im Kern:
   `GET /payments/audit?intent_id=rcv_<ref_hash[:16]>` zeigt `intent_created` +
   **genau ein** `receivable_settled`; `GET /health/payment` bleibt grün;
   `kai-ln-reconcile.timer` meldet danach `ok` ohne Waise.
6. `kai-server` neu starten → Status bleibt `SETTLED`, **kein** zweiter Record.
7. Gate wieder zu: `APP_PAY_ENABLED=false`, Neustart, `/pay/health` → 404.

## 8. Grenzen

* Kein Storno, keine Teilzahlung, keine Rückerstattung — v0.1 kennt nur „bezahlt / nicht".
* `FAILED` ist heute unerreichbar (kein Rail liefert eine endgültige Absage).
* Der Poller läuft **im Serverprozess**. Steht `kai-server`, bestätigt weiterhin der
  15-Minuten-Reconciler — langsamer, aber vollständig.
* Der Basename `requests.jsonl` ist generisch; ein künftiger zweiter `requests.jsonl` an
  anderer Stelle würde vom Stream-Ratchet (der auf Basenames schlüsselt) stillschweigend
  als bekannt durchgewinkt. Beim nächsten Anfassen umbenennen.
