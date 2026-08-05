# Runbook — G0 L402 Demand-Probe Go-Live (operator)

Capital-free **receive** probe over `/oracle/fee-series`. NOTHING here is autonomous —
every step is an operator action. The probe never enables spend (`pay_enabled` stays
**false** throughout). Fully reversible; no capital ever moves.

Pre-registration (price, window, threshold) is fixed in **ADR 0011** — do not change it
once data exists.

## 1. Bake SCOPE-MINIMAL macaroons (HARD requirement — satoshi auflage 4)
On the lnd / RaspiBlitz node:

    lncli bakemacaroon invoices:write invoices:read --save_to kai-invoice.macaroon

NO `admin`, NO `onchain:write`, NO `offchain:write`, NO `peers:write`. This is the only
defense that survives an app bug: even a mis-gated spend is rejected by the **node**.
Install it (mode 600) plus the `tls.cert` (`APP_LN_TLS_CERT_PATH`):

    APP_LN_INVOICE_MACAROON_PATH=/home/ubuntu/kai-secrets/lnd/kai-invoice.macaroon

**Do NOT repoint `APP_LN_MACAROON_PATH` here.** Since W0/PR-A the credentials are
split per capability, but **no consumer is switched yet** — every live path (oracle
mint, earnings booking, value layer) still runs on `APP_LN_MACAROON_*`. Narrowing it
to pure readonly before PR-C would silently kill the receive path. Bake the invoice
credential now so the preflight can PROVE the split; the switch is PR-C. See
`docs/lightning_macaroon_matrix.md`.

## 2. Run the preflight (must be GO)

    python scripts/ln_golive_preflight.py

It probes: node reachable (`getinfo`, read credential); **credential split**
(`APP_LN_MACAROON_*` and `APP_LN_INVOICE_MACAROON_*` are checked separately — one
macaroon for everything can no longer return GO); **macaroon scope** (a raw
`pay_invoice` probe MUST be permission-denied on **both** receive-side credentials —
proving no spend scope); **macaroon can mint** (a raw `add_invoice` probe on the
INVOICE credential MUST succeed; a `readonly.macaroon` has no spend scope BUT also
cannot receive, which would `503` the paid path, so this check catches that trap);
**inbound liquidity** (the node's `remote_balance` must be >= the price — 0 inbound
means nobody can pay, a hard NO-GO; `getinfo`-green does NOT prove this); booking
timer installed; telemetry writable; and `pay_enabled` OFF. Exit 0 /
`"verdict": "GO"` is required before step 3.

## 3. Flip the receive path (operator)
In the Pi `.env` — **NEVER** `pay_enabled`:

    APP_LN_ENABLED=true
    APP_LN_MACAROON_PATH=<unchanged — still the credential every path uses>
    APP_LN_INVOICE_MACAROON_PATH=/home/ubuntu/kai-secrets/lnd/kai-invoice.macaroon
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
