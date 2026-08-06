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

| Pfad / Aktion | Modul | lnd REST | Benötigte lnd-Permission (`lncli bakemacaroon`) |
|---|---|---|---|
| Node-Status / Balances / Channels (Phase 1) | `adapter.py` | GET `/v1/state`,`/v1/getinfo`,`/v1/balance/*`,`/v1/channels`,`/v1/fees` | `info:read offchain:read onchain:read` (= readonly) |
| Invoice erstellen (Receive) | `value_layer.create_invoice` | POST `/v1/invoices` | `invoices:write` |
| BOLT12-Offer (Receive, Sprint 3) | (Sprint 3) | POST `/v2/...offers` | `invoices:write offchain:read` |
| Invoice zahlen / Keysend (Send) | `value_layer.pay_invoice/keysend` | GET `/v1/payreq/{pay_req}` vor POST `/v1/channels/transactions` | `offchain:read offchain:write` |
| On-Chain-Withdraw (Send) | `value_layer.send_coins` | POST `/v1/transactions` | `onchain:write` |
| Channel öffnen | `value_layer.open_channel` | POST `/v1/channels` | `onchain:write offchain:write` |
| Channel schließen | `value_layer.close_channel` | DELETE `/v1/channels/{txid}/{idx}` | `offchain:write onchain:write` |
| Rebalance (PLAN-only) | `value_layer.rebalance_plan` | — (kein Node-Write) | keine (reiner Plan) |

## Verbindliche Macaroon-Aufteilung (Bakery)

| Credential | Env-Paar (`_PATH` / `_HEX`) | Scope in `macaroon_credentials()` | Permissions |
|---|---|---|---|
| `kai-readonly.macaroon` | `APP_LN_MACAROON_*` | `read` (Default) | `info:read offchain:read onchain:read` — keine Write-Rechte |
| `kai-invoice.macaroon` | `APP_LN_INVOICE_MACAROON_*` | `invoice` | `invoices:read invoices:write` — Rechnungen erstellen und eigene Settlements lesen, **kein** Spend |
| `kai-payment.macaroon` | `APP_LN_PAYMENT_MACAROON_*` | `payment` | `offchain:read offchain:write` — ausschließlich BOLT11/Keysend |
| `kai-onchain.macaroon` | `APP_LN_ONCHAIN_MACAROON_*` | `onchain` | `onchain:write` — ausschließlich Withdraw; nur baken, wenn der Pfad wirklich geöffnet wird |
| `kai-channel.macaroon` | `APP_LN_CHANNEL_MACAROON_*` | `channel` | `offchain:write onchain:write` — Channel-Operationen, separat widerrufbar, standardmäßig nicht provisioniert |

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
