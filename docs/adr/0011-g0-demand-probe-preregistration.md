# ADR 0011 — G0 Demand-Probe: Pre-Registration

**Status:** Accepted (2026-06-24)

## Context
The Unified-Lighthouse gate **G0** = a *paying use-case* OR a *proven edge*. The L402
demand probe over `/oracle/fee-series` tests the paying-use-case branch. To keep the
read honest, the success threshold MUST be fixed BEFORE measuring — no moving the
goalpost after data exists.

## Decision (pre-registered, binding)
- **Product:** `GET /oracle/fee-series` — sovereign Bitcoin fee/mempool series, L402-paid.
- **Price:** 100 sats / call (`APP_LN_L402_DEFAULT_PRICE_SAT=100`).
- **Window:** 14 days from the documented go-live date.
- **G0-PASS =** ≥3 settled `kai-oracle:fee-series` payments **AND** from ≥2 distinct
  requester fingerprints **AND** on ≥2 distinct calendar days.
  - The ≥2-fingerprint / ≥2-day floor is the **fraud guard**: a single actor
    self-paying 3× must NOT pass.
- **Distribution:** public listing (Nostr / BTC-dev + an L402 directory). The posting is
  an external operator action; the probe code only produces the listing artifact (see
  `docs/runbooks/ln_g0_golive.md`).

## Honest limitation
LN payer identity is NOT provable. The IP fingerprint is a heuristic (a motivated actor
can use multiple IPs). The threshold measures *plausibly plural, paying demand* — not a
cryptographic identity proof. This is documented, not hidden.

## Consequences
- `evaluate_l402_demand` renders PASS/NO-PASS against exactly these numbers
  (`min_payments=3, min_fingerprints=2, min_days=2, window_days=14`).
- Changing the threshold after data exists invalidates the probe → requires a NEW ADR.
- `pay_enabled` stays **false** throughout; only `receive_enabled` + `l402_enabled` flip.
  The flip is operator-gated — `ln_golive_preflight` must return GO first (incl. the
  scope-minimal macaroon check).
