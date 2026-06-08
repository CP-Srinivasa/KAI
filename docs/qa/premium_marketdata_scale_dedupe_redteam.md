# Premium Market Data, Scale, & Dedupe Red-Team QA Matrix

This document defines the QA Matrix and Red-Team scenarios designed to verify that the Premium Market Data, Scale, Dedupe, and Signal Trail layers operate correctly under stress, garbage data, and edge conditions.

---

## Scenario QA Matrix

The pipeline must reconcile signals from ingestion (Telegram parser) through scale resolution, the operator paper bridge, the paper engine execution, and the target-completion reconciler.

| Scenario | Input Sequence / Trigger | Expected Pipeline Behavior | Expected Terminal State & Reason Code | Dashboard / Trail Status | Audit Logging Verification |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **A. Guter Spot** | `0.356 → 0.357 → 0.358` | Standard execution. Entry matches spot within tolerance; order is filled, opened, and monitored. | `OrderLifecycleState.POSITION_OPEN`<br>Reason: `paper_order_filled` | **OPEN** | Record appended to `bridge_pending_orders.jsonl` with stage `filled` and `paper_execution_audit.jsonl` with `position_opened`. |
| **B. Kurz No Data** | `0.356 → unavailable → 0.356` | Spot is briefly missing. Bridge enters `pending` stage on `unavailable` tick. The next tick restores spot data and fills the order. | `OrderLifecycleState.POSITION_OPEN`<br>Reason: `paper_order_filled` | **OPEN** (initially **PENDING_ENTRY**) | Transition events logged:<br>1. `pending` with `market_data_unavailable`<br>2. `filled` with `paper_order_filled`. |
| **C. Garbage Tick** | `0.356 → 101.94 → 102.10 → 103.00` | A single huge anomaly. Cross-provider disagreement check in `FallbackMarketDataAdapter` tags the point `is_stale=True`. Bridge ignores the bad spot tick and keeps the entry WAITING_FOR_ENTRY. | `OrderLifecycleState.WAITING_FOR_ENTRY`<br>Reason: `entry_not_reached` (or `market_data_unavailable` if stale flag blocks price provider entirely) | **PENDING_ENTRY** | `bridge_pending_orders.jsonl` logs stage `pending` with reason `entry_not_reached` or `market_data_unavailable`. No terminal reject. |
| **D. Mehrere Garbage Ticks** | `0.356 → 101.94 → 102.10 → 103.00` | Sustained garbage ticks. Cross-provider disagreement check continues to tag the feed stale. Price does not trigger entry. Since the entry mode remains pending, it eventually ages out and expires. | `OrderLifecycleState.EXPIRED`<br>Reason: `ttl_exceeded` (after 24 hours or configured TTL) | **EXPIRED** | `bridge_pending_orders.jsonl` appends stage `expired` at the end of the TTL window. No phantom PnL is booked. |
| **E. Raw + Approved** | Same signal ID core received as raw (`telegram_premium_channel`) and approved (`_approved`) source. | De-duplication logic merges the two envelopes using `origin_envelope_id`. The approved envelope is linked as a sub-stage, preventing duplicate paper order entries or double-counted matrix values. | `duplicate` suppression for the redundant envelope.<br>Reason: `already_reconciled` / `already_open` | **CLOSED_TP** / **OPEN** (Single entry) | Approved envelope referenced under `approved_envelope_id` in the trail entry. Single position in `paper_execution_audit.jsonl`. |
| **F. Scale Resolved** | Ingestion price is unavailable, emitting `scale_unknown=True`. Bridge fallback resolves it later when spot is active. | Initial tick: `scale_unknown=True` persists. Subsequent tick: `detect_scale_factor` executes on bridge loop, applies rescale factor (e.g. `1e5`), mutates envelope payload in-place, and updates values to USD scale. | `OrderLifecycleState.POSITION_OPEN` (or pending at correct USD scale)<br>Reason: `paper_order_filled` (scaled) | **OPEN** | `bridge_pending_orders.jsonl` contains `scale_factor_applied` and rescaled payloads. Downstream audit logs show scaled USD prices. |
| **G. Pending Entry** | Limit price not reached, but no error (no risk blocker, no scaling geometry collapse). | The signal is valid structurally and is priced accurately. It remains waiting for the spot price to enter the target zone. | `OrderLifecycleState.WAITING_FOR_ENTRY`<br>Reason: `entry_not_reached` | **PENDING_ENTRY** | `bridge_pending_orders.jsonl` contains stage `pending` with reason `price_outside_tolerance` on every tick. |
| **H. True Scale Review** | Touch-price from completion event diverges from position avg entry price by more than 10x (ratio < 0.1 or > 10.0). | The reconciler detects that the completion message touch price is on an incompatible scale or is corrupted. It refuses to book PnL or close the position, routing to manual review. | `requires_scale_review`<br>Reason: `touch_price_scale_implausible` | **REQUIRES_REVIEW** (or stays **OPEN** with review flag) | `target_completion_audit.jsonl` writes status `requires_scale_review` with detailed scale factors and raw vs. scaled touch prices. |

---

## Detailed Scenario Walkthroughs

### Scenario A: Good Spot
1. **Parser:** Extracts the symbol `BTC/USDT`, entry `64000`, SL `62000`, TP `66000` from Telegram.
2. **Bridge:** Queries the price provider. Current spot is `63950`. This is within the entry tolerance.
3. **Execution:** Orderintent is sent to `paper_engine`. Cash is checked, position is opened with size computed via RiskEngine.
4. **Outcome:** Audit logs write `filled`. Dashboard displays the green **OPEN** status.

### Scenario B: Short No Data
1. **Tick 1:** Spot price query returns `None` (temporary API failure).
2. **Bridge:** Since price is unavailable, it transitions to `pending` with `market_data_unavailable`. Order stays active.
3. **Tick 2:** Spot price query succeeds, returning `63950`.
4. **Bridge:** Evaluates price against entry and executes.
5. **Outcome:** Position is opened successfully. Single brief pending state followed by open state.

### Scenario C: Garbage Tick
1. **Feed:** Spot feed prints `101.94` while the actual price is `0.356`.
2. **Sanity check:** The fallback chain queries Bybit (`0.356`) and Binance (`0.356`) but OKX returns `101.94`.
3. **Disagreement:** The price difference exceeds `MARKET_DATA_PROVIDER_DISAGREEMENT_PCT` (10%). OKX feed is ignored, or the entire tick is flagged as stale (`is_stale=True`).
4. **Bridge:** Skips execution since the point is stale. WAITING_FOR_ENTRY state is preserved.
5. **Outcome:** No incorrect trigger, no execution at bad price.

### Scenario D: Multiple Garbage Ticks
1. **Feed:** Spot feed remains stuck on garbage price `101.94` for a prolonged period.
2. **Disagreement Check:** Always tags incoming quotes as stale.
3. **Bridge:** Keeps the signal pending. No fill is generated.
4. **TTL Expiry:** After `ttl_hours` (default 24), the bridge writes `expired` to the log.
5. **Outcome:** Signal terminates safely as `expired` without ever triggering.

### Scenario E: Raw + Approved
1. **Ingest 1:** Raw signal envelope is written to `telegram_message_envelope.jsonl` with `source="telegram_premium_channel"`.
2. **Ingest 2:** Approved copy is written with `source="telegram_premium_channel_approved"`. Both share the same `origin_envelope_id`.
3. **Bridge:** Filters the incoming envelopes. Since they represent the same trade kernel, only one is allowed to open a position. The `idempotency_key=opbridge:<envelope_id>` blocks duplicate execution.
4. **Outcome:** Only a single position is opened. Dashboard matrix counts it as 1 signal.

### Scenario F: Scale Resolved
1. **Ingest:** Signal arrives for `SWARMS/USDT` with entry price `32450` (channel scale format).
2. **Ingest Time:** Market data is unreachable. The envelope is emitted with `scale_unknown=True` and raw price `32450`.
3. **Bridge Tick:** Market data becomes available. Spot price is `0.0003245`.
4. **Scale Detection:** `detect_scale_factor(32450, 0.0003245)` computes `scale_factor = 1e8`.
5. **Rescaling:** Entry is rescaled to `0.0003245`. Payload is updated.
6. **Execution:** Intent is built using USD-scaled values. Position is opened at the correct price.
7. **Outcome:** Position opens at `0.0003245` instead of `32450`.

### Scenario G: Pending Entry
1. **Parser:** Extracts entry `64000` for BTC/USDT.
2. **Bridge:** Spot is `65500`. This is outside the entry tolerance.
3. **Bridge:** Writes `pending` with `entry_not_reached`.
4. **Outcome:** Signal remains pending until the price drops or TTL expires.

### Scenario H: True Scale Review
1. **Position:** BTC/USDT is open with `avg_entry_price = 63950`.
2. **Ingest:** Completion message arrives: `"🎯 #BTC/USDT has touched 640000..."` (extra zero, factor 10 error).
3. **Reconciler:** Calculates scale factor against position avg entry. `detect_scale_factor(640000, 63950)` yields `1e1` or similar. Since this does not match a recognized scale or results in a scaled close price of `64000` (which is within 10% ratio) but let's check:
   - If ratio is `< 0.1` or `> 10.0`, it fails the ratio check: `close_price / avg_entry = 640000 / 63950 = 10.007` which is `> 10.0`.
4. **Guardrail:** Blocks the close action. Writes `status="requires_scale_review"`.
5. **Outcome:** Position stays open. PnL is not corrupted by a 10x exit price.
