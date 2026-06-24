# Runbook — G0 L402 Demand-Probe Go-Live (operator)

Capital-free **receive** probe over `/oracle/fee-series`. NOTHING here is autonomous —
every step is an operator action. The probe never enables spend (`pay_enabled` stays
**false** throughout). Fully reversible; no capital ever moves.

Pre-registration (price, window, threshold) is fixed in **ADR 0011** — do not change it
once data exists.

## 1. Bake a SCOPE-MINIMAL macaroon (HARD requirement — satoshi auflage 4)
On the lnd / RaspiBlitz node:

    lncli bakemacaroon invoices:write invoices:read --save_to kai-invoice.macaroon

NO `admin`, NO `onchain:write`, NO `offchain:write`, NO `peers:write`. This is the only
defense that survives an app bug: even a mis-gated spend is rejected by the **node**.
Install it as the KAI macaroon (`APP_LN_MACAROON_PATH` / `APP_LN_MACAROON_HEX`) + the
`tls.cert` (`APP_LN_TLS_CERT_PATH`). See `docs/lightning_macaroon_matrix.md`.

## 2. Run the preflight (must be GO)

    python scripts/ln_golive_preflight.py

It probes: node reachable (`getinfo`); **macaroon scope** (a raw `pay_invoice` probe
MUST be permission-denied — proving no spend scope); booking timer installed; telemetry
writable; and `pay_enabled` OFF. Exit 0 / `"verdict": "GO"` is required before step 3.

## 3. Flip the receive path (operator)
In the Pi `.env` — **NEVER** `pay_enabled`:

    APP_LN_ENABLED=true
    APP_LN_L402_ENABLED=true
    APP_LN_RECEIVE_ENABLED=true
    APP_LN_L402_SECRET=<32-byte hex>
    APP_LN_L402_DEFAULT_PRICE_SAT=100

Restart `kai-server`. Enable the earnings-booking timer:

    systemctl enable --now kai-oracle-earnings-booking.timer

## 4. Distribute (external operator action)
Post the listing artifact (below). **Record the go-live date** = the window start.

## 5. Read the verdict

    python scripts/evaluate_l402_demand.py --window-start <go-live-date>

or `GET /dashboard/api/ln/demand`. Decide at the end of the 14-day window per ADR 0011.

## Rollback
`APP_LN_L402_ENABLED=false` (oracle → 503) and/or `APP_LN_RECEIVE_ENABLED=false`;
`systemctl disable --now kai-oracle-earnings-booking.timer`. Reversible, capital-free.

---

## Listing artifact (ready to post)

**KAI Sovereign Fee Oracle — pay-per-call (L402)**

`GET https://<kai-host>/oracle/fee-series` — **100 sats/call** via Lightning (L402).

Verifiable Bitcoin fee/mempool time series — raw observations + deterministic
min/median/max — straight from KAI's own `bitcoind` node. No account, machine-payable:
hit the endpoint, pay the `402` Lightning invoice, retry with
`Authorization: L402 <token>:<preimage>`, get the JSON. Sovereign truth, not a forecast.
