# Lightning Macaroon-Matrix (scope-minimal, NIE admin)

Doktrin: pro Aktion das **engste** lnd-Recht. **NIE** die `admin.macaroon`, **NIE**
die `readonly.macaroon` für einen Write-Pfad. Read, Invoice, Payment, On-chain und
Channel-Management nutzen **getrennte** Macaroons; jeder Wert-Schicht-Pfad ist zusätzlich
hinter `APP_LN_PAY_ENABLED` + dry-run + confirm (B-002 zentraler Send-Gate) gegated.

| Pfad / Aktion | Modul | lnd REST | Benötigte lnd-Permission (`lncli bakemacaroon`) |
|---|---|---|---|
| Node-Status / Balances / Channels (Phase 1) | `adapter.py` | GET `/v1/state`,`/v1/getinfo`,`/v1/balance/*`,`/v1/channels`,`/v1/fees` | `info:read offchain:read onchain:read` (= readonly) |
| Invoice erstellen (Receive) | `value_layer.create_invoice` | POST `/v1/invoices` | `invoices:write` |
| BOLT12-Offer (Receive, Sprint 3) | (Sprint 3) | POST `/v2/...offers` | `invoices:write offchain:read` |
| Invoice zahlen / Keysend / Recovery | `value_layer.pay_invoice/keysend`, `payment_reconciliation` | POST `/v1/channels/transactions`, GET `/v2/router/track/{hash}` | `offchain:read offchain:write` |
| On-Chain-Withdraw (Send) | `value_layer.send_coins` | POST `/v1/transactions` | `onchain:write` |
| Channel öffnen | `value_layer.open_channel` | POST `/v1/channels` | `onchain:write offchain:write` |
| Channel schließen | `value_layer.close_channel` | DELETE `/v1/channels/{txid}/{idx}` | `offchain:write onchain:write` |
| Rebalance (PLAN-only) | `value_layer.rebalance_plan` | — (kein Node-Write) | keine (reiner Plan) |

## Verbindliche Macaroon-Aufteilung (Bakery)
- **`kai-readonly.macaroon`** (`APP_LN_MACAROON_PATH`): `info:read offchain:read onchain:read` — KEINE Write-Rechte.
- **`kai-invoice.macaroon`** (`APP_LN_INVOICE_MACAROON_PATH`): `invoices:read invoices:write` — Rechnungen erstellen und eigene Settlements lesen, kein Spend.
- **`kai-payment.macaroon`** (`APP_LN_PAYMENT_MACAROON_PATH`): `offchain:read offchain:write` — ausschließlich BOLT11/Keysend plus read-only TrackPaymentV2-Recovery.
- **`kai-onchain.macaroon`** (`APP_LN_ONCHAIN_MACAROON_PATH`): `onchain:write` — ausschließlich Withdraw; nur provisionieren, wenn der Pfad tatsächlich geöffnet wird.
- **`kai-channel.macaroon`** (`APP_LN_CHANNEL_MACAROON_PATH`): `offchain:write onchain:write` — Channel-Operationen; separat widerrufbar und standardmäßig nicht provisioniert.
- **`admin.macaroon`** verlässt die Node NIE.

## Reihenfolge der Aktivierung (G1)
1. Nur die für den freizugebenden Pfad erforderlichen Capability-Macaroons baken und nach `/home/ubuntu/kai-secrets/lnd/` (mode 600) kopieren.
2. `APP_LN_MACAROON_PATH` bleibt unverändert read-only; Write-Credentials ausschließlich über die vier Capability-Variablen setzen.
3. Integrationstest ausführen: Invoice-Credential kann Rechnungen erstellen, aber **nicht** zahlen; Payment-Credential kann weder Withdraw noch Channel-Management.
4. Erst dann `APP_LN_PAY_ENABLED=true` — und auch dann bleibt jede Aktion dry-run/Policy/Idempotenz-gegated (B-002/W0-P4).
