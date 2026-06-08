# Premium Execution & UI Prevention Invariants

This document lists the core biological and technical invariants that protect the Premium Execution Bridge, Market Data adapters, and Dashboard displays. Future code edits by any agent (Claude, Codex, etc.) must preserve these invariants.

---

## The 12 QA Invariants & Rules

### 1. Isolation of Garbage Spot Prices
* **Invariant:** A corrupted or garbage spot tick (e.g. flash crash, provider pricing glitch) must never affect scale resolver checks, signal geometry validation, or realized PnL calculations.
* **Mechanism:** Checked by `validate_spot_price` in `premium_market_guard.py`.

### 2. Resilience to Transient Bad Ticks
* **Invariant:** A single bad spot price tick must never terminally reject or abort a signal in `WAITING_FOR_ENTRY` state.
* **Mechanism:** Ignored as `premium_market_price_outlier_rejected` / `premium_bad_tick_ignored` for up to `BAD_TICK_TERMINAL_THRESHOLD` (3) consecutive counts before stably rejecting the signal under `repeated_bad_market_ticks`.

### 3. Curing `scale_unknown` Post-Bridge
* **Invariant:** An envelope marked `scale_unknown=True` at ingestion must clear the flag and persist `scale_unknown=False` once the execution bridge resolves the scale factor.
* **Mechanism:** The bridge writes the rescaled values and sets `scale_unknown=False` which is saved to `bridge_pending_orders.jsonl` and read by the trail builder.

### 4. Guarding Absurd Entry-to-Spot Ratios
* **Invariant:** Resolving a scale factor of `1.0` (direct USD) is prohibited if the ratio between entry price and spot is extreme (>100 or <0.01). Such signals must be rejected at scale check rather than treated as valid.
* **Mechanism:** Handled by `scale_unresolved_or_bad_price` in `premium_market_guard.py` returning `SCALE_UNRESOLVED_EVENT`.

### 5. Deduplication of Raw + Approved Channels
* **Invariant:** A raw signal received from the premium channel and its approved counterpart represent exactly *one* operational business signal.
* **Mechanism:** Deduped via `dedupe_premium_signal_records` using unique hashes, message IDs, or structural hashes within a 15-minute window.

### 6. Separation of Ingest and Execution Outcomes
* **Invariant:** The dashboard matrix must not count parser-only signals (which failed risk checks or global gates) as execution successes.
* **Mechanism:** Fills are strictly audited from paper engine event logs, preventing successful marks for rejected envelopes.

### 7. Display State Invariants on External Signals
* **Invariant:** Envelopes that are merely "accepted" (parsed and allowlisted) must never be displayed as "executed" in the External Signals view. They are waiting, pending, or rejected until an actual position fill happens.

### 8. Headlining the Operative Terminal State
* **Invariant:** The Signal Trail header must state the operative terminal cause (e.g. `RISK_REJECTED` or `ENTRY_DISABLED`) instead of generic headers.

### 9. Isolating `entry_mode` Posture
* **Invariant:** `entry_mode` (disabled/enforce) is a posture constraint. The UI must not claim a signal was blocked by `entry_mode` if it was actually rejected by a specific risk limit or bad geometry.

### 10. Source-Attributed Portfolio PnL
* **Invariant:** Realized PnL reported on the dashboard must remain attributed to its source (autonomous trading vs. premium signal bridge) to keep performance metrics transparent.

### 11. Fair Quality Metrics for Shadow Signals
* **Invariant:** Shadow signals (autonomous strategy candidates that did not execute) must never be graded as trading misses in premium performance scores.

### 12. Scale-Matched Completion Fills
* **Invariant:** Booking completion PnL requires that both the entry fill and the exit touch price are calculated on the same scale, preventing astronomically corrupted realized PnL values.
* **Mechanism:** Verified by the 10x ratio checks in the `target_completion_reconciler.py`.
