# Regtest L402 E2E harness (PR7, real-node)

A throwaway **regtest** Lightning network that proves the full L402 demand-probe
round-trip against a REAL lnd — closing the gap the in-process E2E
(`tests/unit/test_ln_l402_e2e.py`) leaves by mocking the two node touches.

It synthesizes, for FREE (regtest coins), exactly the three things the live preflight
flags as missing on the real RaspiBlitz node:
- an **`invoices:write` macaroon** (baked on `alice` — proves minting works, which the
  live `readonly.macaroon` cannot),
- a **payer** (`bob`),
- **inbound liquidity** (a channel `bob -> alice`).

Then `driver.py` runs KAI's OWN code (`LndRestClient`, `l402`, `earnings_ledger`):

    mint(invoices:write) -> token -> bob pays -> settle -> L402 verify -> book

## Run (from repo root)

    bash test/regtest-ln/run.sh          # spins up, drives, tears down
    KEEP=1 bash test/regtest-ln/run.sh   # leave the stack up

Requires Docker. On Git Bash/Windows the script sets `MSYS_NO_PATHCONV=1` (container
paths get mangled otherwise). Capital-free; tear down with
`docker compose -p kai-regtest-ln down -v`.

Not collected by CI (no LN stack in CI) — this is an operator/dev harness; the
CI-runnable regression guard is the in-process E2E.
