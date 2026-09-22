# Runbook — Sendepfad scharf schalten (D-277, S3)

Zweck: `APP_LN_PAY_ENABLED=true` nur mit Beweis. Drei Beweise, sonst Flag zurueck.
Alles hier ist Operator-Handlung auf der Pi (`ubuntu@192.168.178.23`, Node `APP_LN_HOST`).
Der Flag-Flip ist Kapital-relevant und wird NICHT aus einer Claude-Sitzung ausgefuehrt.

## 0. Vorbedingungen (ohne die kein Schritt 1)

- Runtime enthaelt S1 (`/v2/router/send`) und S2 (`/pay`, armierte Preflight-Fakten) — Deploy 17./18.09.
- Die externe Wallet-App ist ein **getrenntes** D-277-(4)-Alltags-UX-Ziel,
  keine Voraussetzung fuer den KAI-`/pay`-Self-Use-Pilot. Ihre direkte Nutzung
  am selben Node darf bis zur nachgewiesenen Budgetdurchsetzung nicht als
  Daily-Driver freigegeben werden:
  ein auf RPC-Rechte eingeschraenktes lnd-Macaroon begrenzt keine Satoshi-Summe
  (D-278). Budgetmechanismus, Grenze und Negativprobe separat dokumentieren.
- `APP_LN_SCB_PATH` zeigt auf eine aktuelle, vom Node verifizierte Kopie;
  `kai-ln-scb-monitor.timer` ist aktiviert und liefert `stable`. Der SCB-Exporter
  aus #1003 läuft erst nach Konfiguration und Timer-Aktivierung dauerhaft.
- Für den Sendetest eine **Rechnung eines anderen Nodes** bereithalten. Eine
  Wallet-App am selben lnd-Node ist kein unabhängiger Zahlungsempfänger.
- Inbound-Liquiditaet ist fuer den Sendepfad irrelevant; fuer Empfang siehe `ln_inbound_swap.md`.

## 1. Konfiguration (`.env`, Sicherung zuerst)

```
bash /home/ubuntu/ai_analyst_trading_bot/scripts/env_backup.sh prearm
```

Legt `.env.bak-<UTC>-prearm` neben der `.env` an (Rechte 600) und behaelt nur
die drei neuesten Sicherungen — nie per `cp` sichern.

Setzen bzw. pruefen:

| Schluessel | Wert | Warum |
|---|---|---|
| `APP_PAYMENT_MODE` | `live` | shadow sendet nie (`PaymentService.execute` verweigert) |
| `APP_PAYMENT_PER_PAYMENT_MAX_SAT` | z. B. `10000` | Cap pro Zahlung |
| `APP_PAYMENT_DAILY_HARD_CAP_SAT` | z. B. `25000` | Tages-Cap |
| `APP_PAYMENT_APPROVAL_THRESHOLD_SAT` | z. B. `1` | HOTP ab 1 sat fuer die Beweisphase |
| `APP_PAYMENT_FEE_LIMIT_DEFAULT_PPM` / `_MAX_SAT` | `3000` / `200` | Client verweigert Send ohne Fee-Grenze |
| `APP_PAYMENT_FEE_LIMIT_MIN_SAT` | `3` fuer erneuten 10-sat-Test (Default `1`) | Untergrenze fuer Kleinstzahlungen; nie hoeher als `_MAX_SAT` setzen. Vorher neue Routenschaetzung pruefen; hoeheres Limit garantiert keine Route. |
| `APP_PAYMENT_PURPOSES_ALLOWED` | enthaelt `operator_pay_invoice` | sonst lehnt die Policy `/pay` ab |
| `APP_PAYMENT_DESTINATION_ALLOWLIST` | SHA-256 des `destination`-Pubkeys aus `lncli decodepayreq <bolt11>` der externen Testrechnung (UTF-8-Text, lowercase Hex) | leer oder ohne passenden Hash lehnt die Policy jede `/pay`-Rechnung ab; **nicht** den Hash der BOLT11-Rechnung oder den `payment_hash` eintragen |
| `APP_LN_PAYMENT_MACAROON_PATH` | Macaroon mit `offchain:write` | eigenes Credential, nie das Read-Macaroon |
| `APP_LN_SCB_PATH` | Pfad der SCB-Kopie | Preflight verlangt Alter <= `APP_LN_SCB_MAX_AGE_SECONDS` |
| `APP_LN_PAY_ENABLED` | **noch `false`** | erst nach Schritt 2 |

## 2. Preflight muss GO sein — zweimal

Erst mit `APP_LN_PAY_ENABLED=false` (Empfangs-Regime, Bestand):

```
cd /home/ubuntu/current && .venv/bin/python -m scripts.ln_golive_preflight; echo exit=$?
```

Dann `APP_LN_PAY_ENABLED=true` setzen (noch KEIN Restart) und erneut laufen lassen. Im armierten
Regime muessen zusaetzlich gruen sein: `macaroon_send_capable`, `router_send_supported`
(lnd >= 0.11, Version aus `getinfo`), `scb_backup_fresh`, `payment_mode_live`,
`fee_cap_configured`, `pay_purpose_allowed`, `pay_destination_allowlisted`. Ein `blocking` != `[]` heisst: Flag zurueck auf
`false`, Ursache beheben, von vorn.
Der Preflight belegt nur, dass die Allowlist nicht leer und syntaktisch gueltig ist;
ob die konkrete Rechnung zum Hash passt, prueft erst die Zahlungs-Policy.

## 3. Restart und Smoke

```
sudo systemctl restart kai-server
curl -s http://127.0.0.1:8000/health | python3 -m json.tool | grep -E 'runtime_commit|lightning'
```

Telegram: `/pay` ohne Argument muss die Usage zeigen; `/pay status` „Kein /pay-Vorgang".

## 4. Die drei Beweise (in dieser Reihenfolge, jeder ins DECISION_LOG)

1. **1-sat-Send an eine externe Rechnung** (Empfänger liegt auf einem anderen
   Lightning-Node; dessen Wallet erzeugt `lnbc10n...`):
   `/pay <bolt11>` → Vorschau zeigt Betrag 1 sat, Gebuehr-Limit, `AWAITING_APPROVAL` →
   `/pay ok <hotp>` → `✅ Bezahlt … SETTLED`. Journal: `artifacts/payments/payment_journal.jsonl`
   traegt `rail_responded` + `settled` mit `proof_hash`.
2. **Send ueber Cap wird geblockt**: Rechnung ueber `APP_PAYMENT_PER_PAYMENT_MAX_SAT` →
   `/pay <bolt11>` → `⛔ Policy lehnt ab (…)`, kein Intent im Zustand AUTHORIZED, Node unberuehrt.
3. **Gebührengrenze mit positivem Limit belegen**: Für eine neue externe Rechnung
   eine Route ermitteln, deren geschätzte Gebühr über einem bewusst niedrigen,
   aber **positiven** Limit liegt. Das Limit mit dem bestehenden Config-Backup-
   und Restart-Verfahren setzen, Preflight erneut GO, dann `/pay` ausführen.
   Als Ablehnungsbeweis zählt nur ein vom Node bestätigter Gebühren-/Routenfehler
   ohne Settlement; Journal und Node müssen übereinstimmen. Findet der Node eine
   günstigere Route und settlet, darf die tatsächliche Gebühr das Limit nicht
   überschreiten — das belegt die Obergrenze, **nicht** die geforderte Ablehnung.
   Ohne geeignete Route bleibt Beweis 3 offen. Limit anschließend zurücksetzen,
   Restart und Preflight wiederholen. Der Unit-Test
   `tests/unit/test_ln_router_send.py` belegt zusätzlich, dass der positive
   `fee_limit_sat` an `SendPaymentV2` übergeben wird.

`APP_PAYMENT_FEE_LIMIT_MAX_SAT=0` ist eine **separate Konfigurations-Negativprobe**:
Boot-/Preflight-/Policy-Deny ist korrekt, beweist aber keine Durchsetzung eines
gültigen Gebührenlimits am Node.

Fehlt ein Beweis oder faellt einer anders aus: `APP_LN_PAY_ENABLED=false`, Restart, Eintrag
mit dem Befund. Kein zweiter Versuch am selben Tag ohne Ursache.

## 5. Betriebsnachweis (7 Tage)

Der erste **72-Stunden-Self-Use-Pilot** beginnt erst mit dem ersten extern
nachgewiesenen `SETTLED` aus Schritt 4. Drei Tage echte Nutzung und Reconcile-/
SCB-Beobachtung sind ein Zwischenbefund, keine vorgezogene Freigabe fuer Trading,
L2-Shadow oder autonome L5-Aktionen. Ohne Zahlung kein T0; fehlende Tage zaehlen
nicht als erfolgreiche Nutzung.

Taeglich: ein echter `/pay`-Send (Kleinbetrag), `kai-ln-reconcile` laeuft (Timer pruefen:
`systemctl list-timers | grep kai-ln`), `kai-ln-scb-monitor` meldet `stable`. Ergebnisse als
eine Zeile pro Tag ins DECISION_LOG (D-277-Nachtrag): Betrag, Gebührenlimit und
tatsächliche Gebühr, Ergebnis (`SETTLED`/`FAILED_FINAL`/ungeklärt), Dauer,
Reconcile-Status und SCB-Status. Fehler und unbekannte Ergebnisse zählen mit;
bei sieben Tagen mit wenigen Sends entsteht ein Pilotnachweis, noch keine
statistisch belastbare Erfolgsquote. Erst danach Trading-Rails thematisieren.

## Rollback

`APP_LN_PAY_ENABLED=false` in `.env`, `sudo systemctl restart kai-server` — D-242-Zustand.
Journal bleibt append-only; offene Intents klaert `recover_on_start`.
