# Shadow-Candidate-Ledger (Phase B) — Runbook

**Status:** OFF by default. Pure read-only diagnostics. Never trades.
**Purpose:** learn the entry signal while `EXECUTION_ENTRY_MODE=disabled`, without
re-enabling paper/probe/live. For every signal the autonomous loop WOULD have
entered, record a hypothetical candidate, then resolve forward returns + MAE/MFE
from market klines and classify the root cause.

Closes the Phase-A instrumentation gap (MAE/MFE was only 52/110 reconstructable
retrospectively; regime per-trade not at all). See
`artifacts/root_cause_stopout_cascade_20260602.md`.

## Components

- `app/observability/shadow_candidate_ledger.py` — IO-free core (compute, record,
  idempotent resolver, report + heuristic classifier).
- `app/observability/shadow_resolver.py` — Binance 1m-kline fetcher + wrapper.
- `run_cycle` hook (gated by `EXECUTION_SHADOW_DIAGNOSTICS`).
- CLI: `trading shadow-resolve`, `trading shadow-report`.
- `deploy/systemd/kai-shadow-resolver.{service,timer}` — opt-in, not installed.

## Data flow

```
signal emitted (entry_mode=disabled + EXECUTION_SHADOW_DIAGNOSTICS=true)
  -> ShadowCandidate appended to artifacts/shadow_candidate_ledger.jsonl
     (entry/SL/TP/geometry/regime/gate-verdict; NO fill/order/position)
  -> trading shadow-resolve (after the 60-min window elapses)
     -> Binance 1m klines -> forward returns 1m/5m/15m/60m + MAE/MFE
     -> artifacts/shadow_candidate_resolved.jsonl  (idempotent)
  -> trading shadow-report
     -> MAE/MFE + forward-return distribution + splits + primary_class
```

`primary_class` ∈ {ADVERSE_SELECTION, STOP_IN_NOISE_BAND, TP_UNREACHABLE,
PROFIT_NOT_HARVESTED, INSUFFICIENT_DATA, UNCLASSIFIED}. It is a HINT over the raw
distribution the report carries — not a verdict. Do NOT change exit/stop/TP/regime
behaviour off this alone; the report can model hypothetical exits, but production
behaviour stays unchanged until a new candidate is replay-validated.

## Activation (manual, deliberate)

1. Set on the Pi `.env` (keeps entry trading OFF):
   ```
   EXECUTION_ENTRY_MODE=disabled
   EXECUTION_SHADOW_DIAGNOSTICS=true
   ```
   Restart `kai-server`; the timer-driven paper loop picks up the flag next tick.
2. Enable the resolver cadence (opt-in):
   ```
   sudo cp deploy/systemd/kai-shadow-resolver.{service,timer} /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now kai-shadow-resolver.timer
   ```
3. Read the report any time:
   ```
   python -m app.cli.main trading shadow-report          # table
   python -m app.cli.main trading shadow-report --json   # machine
   ```

## Deactivation / rollback

- Stop recording: set `EXECUTION_SHADOW_DIAGNOSTICS=false` (or unset) + restart
  `kai-server`. The loop returns to the cheap entry-mode early-return.
- Stop resolving: `sudo systemctl disable --now kai-shadow-resolver.timer`.
- The ledger files are append-only diagnostics; safe to archive/delete.

## Invariants (test-pinned)

- Flag OFF → loop writes no candidate, no market-data fetch.
- Flag ON + disabled → exactly one candidate per emitted signal; NO paper fill,
  no order, no position.
- Resolver: idempotent; missing klines leave a candidate pending (no crash);
  never calls paper_engine / exchange / order-router.
- Report: handles empty / pending-only / resolved ledgers; missing regime →
  bucketed under its own key, classifier degrades to INSUFFICIENT_DATA when
  n < 20.
