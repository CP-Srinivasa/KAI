# Lightning Macaroon-Matrix (scope-minimal, NIE admin)

Doktrin: pro Aktion das **engste** lnd-Recht. **NIE** die `admin.macaroon`, **NIE**
die `readonly.macaroon` für einen Write-Pfad. Read-only (Phase 1) und Wert-Schicht
(Sprint 4) nutzen **getrennte** Macaroons; der Wert-Schicht-Pfad ist zusätzlich
hinter `APP_LN_PAY_ENABLED` + dry-run + confirm (B-002 zentraler Send-Gate) gegated.

| Pfad / Aktion | Modul | lnd REST | Benötigte lnd-Permission (`lncli bakemacaroon`) |
|---|---|---|---|
| Node-Status / Balances / Channels (Phase 1) | `adapter.py` | GET `/v1/state`,`/v1/getinfo`,`/v1/balance/*`,`/v1/channels`,`/v1/fees` | `info:read offchain:read onchain:read` (= readonly) |
| Invoice erstellen (Receive) | `value_layer.create_invoice` | POST `/v1/invoices` | `invoices:write` |
| BOLT12-Offer (Receive, Sprint 3) | (Sprint 3) | POST `/v2/...offers` | `invoices:write offchain:read` |
| Invoice zahlen / Keysend (Send) | `value_layer.pay_invoice/keysend` | POST `/v1/channels/transactions` | `offchain:write` |
| On-Chain-Withdraw (Send) | `value_layer.send_coins` | POST `/v1/transactions` | `onchain:write` |
| Channel öffnen | `value_layer.open_channel` | POST `/v1/channels` | `onchain:write offchain:write` |
| Channel schließen | `value_layer.close_channel` | DELETE `/v1/channels/{txid}/{idx}` | `offchain:write onchain:write` |
| Rebalance (PLAN-only) | `value_layer.rebalance_plan` | — (kein Node-Write) | keine (reiner Plan) |

## Empfohlene Macaroon-Aufteilung (Bakery)
- **`kai-readonly.macaroon`** (`APP_LN_MACAROON_PATH`, Phase 1, heute live): `info:read offchain:read onchain:read` — KEINE Write-Rechte.
- **`kai-value.macaroon`** (separater Pfad, erst bei G1 scharf): exakt die Write-Permissions der scharf geschalteten Aktionen, sonst nichts. Beispiel nur Receive+Pay (kein On-Chain): `invoices:write offchain:write`.
- **`admin.macaroon`** verlässt die Node NIE.

## Reihenfolge der Aktivierung (G1)
1. `kai-value.macaroon` mit minimalem Scope baken, nach `/home/ubuntu/kai-secrets/lnd/` (mode 600).
2. `APP_LN_MACAROON_PATH` für den Wert-Schicht-Pfad zeigt auf `kai-value.macaroon` (getrennt vom readonly-Pfad).
3. Erst dann `APP_LN_PAY_ENABLED=true` — und auch dann bleibt jede Aktion dry-run/confirm-gegated (B-002).
