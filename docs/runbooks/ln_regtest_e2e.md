# Runbook — L402 Demand-Flow E2E (regtest / signet)

Two layers of end-to-end coverage for the L402 demand pipeline (U1–U5):

## 1. In-process E2E (automated, CI)
`tests/unit/test_ln_l402_e2e.py` drives the FULL round-trip in one process —
mint → `402` challenge → demand telemetry → paid retry → `200` → earnings booking →
demand verdict — with only the two genuine node touches mocked (invoice mint +
`ListInvoices`). It runs on every CI build and is the regression guard that U1–U5
compose.

    pytest tests/unit/test_ln_l402_e2e.py

## 2. Real-node E2E (operator, regtest or signet)
Proves the SAME flow against a real lnd — the mocked node touches become real. Use a
throwaway **regtest** (instant blocks) or **signet**; NO real coins move.

### Setup
- `bitcoind -regtest` (or signet) + lnd (the payee = KAI's node) wired to it.
- A payer: a 2nd lnd or an `lncli` wallet with a funded channel to the payee.
- Bake the scope-minimal invoice macaroon (`docs/runbooks/ln_g0_golive.md` §1) and point
  KAI at the node (`APP_LN_*`). Flip `APP_LN_ENABLED` / `APP_LN_L402_ENABLED` /
  `APP_LN_RECEIVE_ENABLED` = true, set `APP_LN_L402_SECRET`. **Never** `APP_LN_PAY_ENABLED`.

### Run
1. **Readiness:** `python scripts/ln_golive_preflight.py` → must be `GO` (includes the
   macaroon-scope probe: a `pay_invoice` probe MUST be permission-denied).
2. **Challenge:** `curl -i https://<host>/oracle/fee-series` → `402` +
   `WWW-Authenticate: L402 token="…", invoice="…"`.
3. **Pay** the bolt11 from the payer (`lncli payinvoice <bolt11>`) → learn the preimage.
4. **Access:** `curl -H "Authorization: L402 <token>:<preimage>" …/oracle/fee-series` →
   `200` + the fee-series JSON.
5. **Book:** the `kai-oracle-earnings-booking.timer` (or `python
   scripts/book_oracle_earnings.py`) books the settled invoice into the earnings ledger.
6. **Verdict:** `python scripts/evaluate_l402_demand.py` → the payment appears under
   `settled_payments`.

### Teardown
Tear down the regtest chain; nothing persists. Fully reversible, capital-free.

> The G0 go-live (mainnet, public listing, 14-day measurement) is a SEPARATE operator
> decision — see `docs/runbooks/ln_g0_golive.md` and ADR 0011. This harness only proves
> the mechanism end-to-end; it does not start the demand probe.
