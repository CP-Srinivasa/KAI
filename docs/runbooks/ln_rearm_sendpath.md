# Runbook — Sendepfad scharf schalten (D-277, S3)

Zweck: `APP_LN_PAY_ENABLED=true` nur mit Beweis. Drei Beweise, sonst Flag zurueck.
Alles hier ist Operator-Handlung auf der Pi (`ubuntu@192.168.178.23`, Node `APP_LN_HOST`).
Der Flag-Flip ist Kapital-relevant und wird NICHT aus einer Claude-Sitzung ausgefuehrt.

## 0. Vorbedingungen (ohne die kein Schritt 1)

- Runtime enthaelt S1 (`/v2/router/send`) und S2 (`/pay`, armierte Preflight-Fakten) — Deploy 17./18.09.
- Wallet-App (Zeus/Blixt) mit Budget-Macaroon am selben Node eingerichtet — die Alltags-UX
  (Saldo, QR, Historie) wird NICHT in KAI gebaut (D-277 (4)).
- Inbound-Liquiditaet ist fuer den Sendepfad irrelevant; fuer Empfang siehe `ln_inbound_swap.md`.

## 1. Konfiguration (`.env`, Sicherung zuerst)

```
cp /home/ubuntu/current/.env /home/ubuntu/.env.bak-$(date -u +%Y%m%d)-prearm
```

Setzen bzw. pruefen:

| Schluessel | Wert | Warum |
|---|---|---|
| `APP_PAYMENT_MODE` | `live` | shadow sendet nie (`PaymentService.execute` verweigert) |
| `APP_PAYMENT_PER_PAYMENT_MAX_SAT` | z. B. `10000` | Cap pro Zahlung |
| `APP_PAYMENT_DAILY_HARD_CAP_SAT` | z. B. `25000` | Tages-Cap |
| `APP_PAYMENT_APPROVAL_THRESHOLD_SAT` | z. B. `1` | HOTP ab 1 sat fuer die Beweisphase |
| `APP_PAYMENT_FEE_LIMIT_DEFAULT_PPM` / `_MAX_SAT` | `3000` / `200` | Client verweigert Send ohne Fee-Grenze |
| `APP_PAYMENT_PURPOSES_ALLOWED` | enthaelt `operator_pay_invoice` | sonst lehnt die Policy `/pay` ab |
| `APP_LN_PAYMENT_MACAROON_PATH` | Macaroon mit `offchain:write` | eigenes Credential, nie das Read-Macaroon |
| `APP_LN_SCB_PATH` | Pfad der SCB-Kopie | Preflight verlangt Alter <= `APP_LN_SCB_MAX_AGE_SECONDS` |
| `APP_LN_PAY_ENABLED` | **noch `false`** | erst nach Schritt 2 |

## 2. Preflight muss GO sein — zweimal

Erst mit `APP_LN_PAY_ENABLED=false` (Empfangs-Regime, Bestand):

```
cd /home/ubuntu/current && python scripts/ln_golive_preflight.py; echo exit=$?
```

Dann `APP_LN_PAY_ENABLED=true` setzen (noch KEIN Restart) und erneut laufen lassen. Im armierten
Regime muessen zusaetzlich gruen sein: `macaroon_send_capable`, `router_send_supported`
(lnd >= 0.11, Version aus `getinfo`), `scb_backup_fresh`, `payment_mode_live`,
`fee_cap_configured`, `pay_purpose_allowed`. Ein `blocking` != `[]` heisst: Flag zurueck auf
`false`, Ursache beheben, von vorn.

## 3. Restart und Smoke

```
sudo systemctl restart kai-server
curl -s http://127.0.0.1:8000/health | python3 -m json.tool | grep -E 'runtime_commit|lightning'
```

Telegram: `/pay` ohne Argument muss die Usage zeigen; `/pay status` „Kein /pay-Vorgang".

## 4. Die drei Beweise (in dieser Reihenfolge, jeder ins DECISION_LOG)

1. **1-sat-Send an eine externe Rechnung** (eigene Wallet-App erzeugt `lnbc10n...`):
   `/pay <bolt11>` → Vorschau zeigt Betrag 1 sat, Gebuehr-Limit, `AWAITING_APPROVAL` →
   `/pay ok <hotp>` → `✅ Bezahlt … SETTLED`. Journal: `artifacts/payments/payment_journal.jsonl`
   traegt `rail_responded` + `settled` mit `proof_hash`.
2. **Send ueber Cap wird geblockt**: Rechnung ueber `APP_PAYMENT_PER_PAYMENT_MAX_SAT` →
   `/pay <bolt11>` → `⛔ Policy lehnt ab (…)`, kein Intent im Zustand AUTHORIZED, Node unberuehrt.
3. **Send ueber Fee-Limit wird abgelehnt**: `APP_PAYMENT_FEE_LIMIT_MAX_SAT=0` temporaer setzen,
   Restart, `/pay <bolt11>` → Preflight/Policy `fee_limit_required` verweigert; danach Wert
   zuruecksetzen und Restart. (Alternative ohne Restart: Preflight Schritt 2 zeigt
   `fee_cap_configured` rot.)

Fehlt ein Beweis oder faellt einer anders aus: `APP_LN_PAY_ENABLED=false`, Restart, Eintrag
mit dem Befund. Kein zweiter Versuch am selben Tag ohne Ursache.

## 5. Betriebsnachweis (7 Tage)

Taeglich: ein echter `/pay`-Send (Kleinbetrag), `kai-ln-reconcile` laeuft (Timer pruefen:
`systemctl list-timers | grep kai-ln`), `kai-ln-scb-monitor` meldet `stable`. Ergebnisse als
eine Zeile pro Tag ins DECISION_LOG (D-277-Nachtrag). Erst danach Trading-Rails thematisieren.

## Rollback

`APP_LN_PAY_ENABLED=false` in `.env`, `sudo systemctl restart kai-server` — D-242-Zustand.
Journal bleibt append-only; offene Intents klaert `recover_on_start`.
