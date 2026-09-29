# Lightning Macaroon-Matrix (scope-minimal, NIE admin)

Doktrin: pro Aktion das **engste** lnd-Recht. **NIE** die `admin.macaroon`, **NIE**
die `readonly.macaroon` für einen Write-Pfad. Read, Invoice, Payment, On-Chain und
Channel-Management nutzen **getrennte** Macaroons; jeder Wert-Schicht-Pfad ist
zusätzlich hinter `APP_LN_PAY_ENABLED` + dry-run + confirm (B-002 zentraler
Send-Gate) gegated.

> **Stand W0/PR-C (2026-08-06):** Die fünf Credentials sind gebacken, vom
> Preflight bestätigt und an ihre jeweiligen Konsumenten verdrahtet. Der
> öffentliche Invoice-Mint benutzt ausschließlich das Invoice-Credential; der
> Sendepfad materialisiert sein Payment-Credential nur bei
> `APP_LN_PAY_ENABLED=true`. Der Read-Scope bleibt der Default ausschließlich für
> Lesepfade — Write-Scopes fallen niemals auf ihn zurück.
>
> **Nachtrag ADR 0018 §12 (2026-09-04, Altpfad-Rückbau):** `value_layer.py` gibt
> es nicht mehr. Von den sechs Write-Pfaden unten haben noch **zwei** einen
> Aufrufer: der Mint (`lightning/receive_gate.create_invoice`, Invoice-Scope) und
> die Zahlung (`payments/rails/lightning.py::pay`, Payment-Scope). Keysend,
> On-Chain-Withdraw und beide Channel-Verben sind ADR §1 DEFERRED und aus dem
> Code entfernt; ihre Zeilen bleiben hier stehen, weil die Matrix die
> **Bakery-Vorschrift** ist — welches Recht ein Credential tragen darf, ändert
> sich nicht dadurch, dass gerade niemand es benutzt. `onchain`/`channel` sind
> damit deklariert, aber ohne Konsument: wer sie neu verdrahtet, findet die
> Vorschrift hier und muss sie nicht erraten.

| Pfad / Aktion | Modul | lnd REST | Benötigte lnd-Permission (`lncli bakemacaroon`) |
|---|---|---|---|
| Node-Status / Balances / Channels (Phase 1) | `adapter.py` | GET `/v1/state`,`/v1/getinfo`,`/v1/balance/*`,`/v1/channels`,`/v1/fees` | `info:read offchain:read onchain:read` (= readonly) |
| Invoice erstellen (Receive) | `receive_gate.create_invoice` | POST `/v1/invoices` | `invoices:write` |
| BOLT12-Offer (Receive, Sprint 3) | (Sprint 3) | POST `/v2/...offers` | `invoices:write offchain:read` |
| Invoice zahlen (Send) | `payments/rails/lightning.py::pay` | GET `/v1/payreq/{pay_req}` vor POST `/v2/router/send` (SendPaymentV2, D-277) | `offchain:read offchain:write` |
| On-Chain-Withdraw (Send) | — (DEFERRED, kein Aufrufer) | POST `/v1/transactions` | `onchain:write` |
| Channel öffnen | — (DEFERRED, kein Aufrufer) | POST `/v1/channels` | `onchain:write offchain:write` |
| Channel schließen | — (DEFERRED, kein Aufrufer) | DELETE `/v1/channels/{txid}/{idx}` | `offchain:write onchain:write` |
| Rebalance (PLAN-only) | — (DEFERRED, kein Aufrufer) | — (kein Node-Write) | keine (reiner Plan) |

## Verbindliche Macaroon-Aufteilung (Bakery)

| Credential | Env-Paar (`_PATH` / `_HEX`) | Scope in `macaroon_credentials()` | Permissions |
|---|---|---|---|
| `readonly.macaroon` (auf dem Pi `kai-secrets/lnd/readonly.macaroon`; seit 2026-09-29 KAI-eigen, `root_key_id 101`, nicht mehr der lnd-Standard) | `APP_LN_MACAROON_*` | `read` (Default) | `address:read info:read invoices:read macaroon:read message:read offchain:read onchain:read peers:read signer:read` — keine Write-Rechte |
| `kai-invoice.macaroon` (seit 2026-09-29 `root_key_id 102`) | `APP_LN_INVOICE_MACAROON_*` | `invoice` | `info:read invoices:read invoices:write offchain:read onchain:read` — Rechnungen erstellen und Settlements lesen, **kein** Spend |
| `kai-account.macaroon` (litd-Account `kai`, seit 2026-09-29 ID `3c64c5625e2821bc`) | `APP_LN_PAYMENT_MACAROON_*` | `payment` | `info:read invoices:read,write offchain:read,write onchain:read peers:read` — Senden nur bis zum Account-Budget, das litd im Node durchsetzt (D-293); Budget nur per `kai-ln-budget` am Laptop |
| ~~`kai-payment.macaroon`~~ | — | — | 2026-09-26 von der Pi gelöscht, seit dem Neuaufbau 2026-09-29 am Node **ungültig** (D-294) |
| `kai-onchain.macaroon` | `APP_LN_ONCHAIN_MACAROON_*` | `onchain` | `onchain:write` — ausschließlich Withdraw; nur baken, wenn der Pfad wirklich geöffnet wird |
| `kai-channel.macaroon` | `APP_LN_CHANNEL_MACAROON_*` | `channel` | `offchain:write onchain:write` — Channel-Operationen, separat widerrufbar, standardmäßig nicht provisioniert |

- **`kai-cockpit.macaroon` — ENTFERNT 2026-09-17 (Operator-Entscheid).** 154 B, gebacken 2026-07-01 für die erste Armierung der Wert-Schicht (laut Hinweistext in `web/src/pages/Node.tsx`: `invoices` + `channel-write`, erster Channel 400k über diesen Pfad). Vor dem Löschen read-only geprüft: keine systemd-Unit, kein Broker, kein Cron, keine `.env`-Variable (auch nicht in den Sicherungen), kein Code-Pfad, kein Skript, kein offener Handle — einzige Erwähnung ist der historische UI-Text. **Nicht ersetzt**, keine neue Datei. Die Datei-Entfernung widerruft das Macaroon am Node NICHT; ein Widerruf der Root-Key-ID würde alle Macaroons mit derselben ID (typisch 0, also auch `readonly`/`invoice`) mit ungültig machen und ist deshalb nur nach Prüfung am Node vorzunehmen. **Seit dem Neuaufbau 2026-09-29 (D-294) ungültig**, weil die alte Wurzel `0` nicht mehr existiert.
- **Neuaufbau 2026-09-29 (D-294):** Alle Wurzeln sind neu. Jeder Zweck hat eine eigene `root_key_id`: 101 lesen, 102 Rechnungen, litd-Account. Einzelne KAI-Schlüssel lassen sich deshalb künftig gezielt per `lncli deletemacaroonid <id>` widerrufen, ohne den Rest zu treffen. Die Wurzel `0` kann lnd **nicht** löschen (`ErrDeletionForbidden`), siehe Runbook `ln_kai_account_budget.md` §11.
- **`admin.macaroon`** verlässt die Node NIE.
- **Kein Write-Fallback:** `macaroon_credentials()` promotet ein fehlendes
  Capability-Credential niemals auf das Read-Credential. Ein nicht provisionierter
  Scope scheitert laut in `_build_client` (`LightningUnavailableError: no macaroon
  configured`) statt still mit zu vielen Rechten zu laufen.

## Reihenfolge der Aktivierung
1. Nur die Capability-Macaroons baken, die der freizugebende Pfad wirklich braucht;
   nach `/home/ubuntu/kai-secrets/lnd/` (mode 600).
2. Die zugehörigen `APP_LN_<CAP>_MACAROON_PATH` setzen. `APP_LN_MACAROON_PATH`
   dabei **nicht** verändern — es bleibt bis PR-C das real benutzte Credential.
3. `python scripts/ln_golive_preflight.py` → GO. Der Preflight prüft Read- und
   Invoice-Credential getrennt und probt `pay_invoice` gegen **beide**
   Empfangs-Credentials; „ein Macaroon für alles" kann damit kein GO mehr liefern.
4. Erst nach PR-C (Konsumenten-Umverdrahtung) kann `APP_LN_MACAROON_*` auf echtes
   Readonly verengt werden.
5. Erst dann `APP_LN_PAY_ENABLED=true` — und auch dann bleibt jede Aktion
   dry-run/Policy/Confirm-gegated (B-002).
