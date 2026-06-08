# Antigravity QA Verification: Premium Guard & Deduplication Layers

This document summarizes the independent verification of **Claude PR #193** and **Codex Commit `c11f9cff`**, confirming the successful resolution of critical market data outliers, scale lifecycles, and deduplication bugs.

---

## 1. Verified Commits & PRs
* **Claude PR #193 / Codex Commit:** `c11f9cff` ("fix(premium): guard market data scale and dedupe raw approved signals")
* **Verification Date:** 2026-06-08
* **Branch:** `antigravity/premium-market-scale-redteam` (merged with codex/premium-market-scale-dedupe-guard)

---

## 2. Test Execution Summary
All 51 unit and integration tests are verified and fully green under the Windows environment (exit code `0`):

```bash
python -m pytest tests/unit/test_premium_market_data_outliers.py tests/unit/test_premium_scale_lifecycle.py tests/unit/test_premium_signal_dedupe.py tests/unit/test_premium_signal_trail.py tests/unit/test_api_signals.py tests/integration/test_premium_pipeline_e2e.py
```

### Key Test Outcomes:
* `test_skyai_garbage_tick_is_rejected_without_terminal_state` (PASSED)
* `test_three_consecutive_bad_ticks_terminalize_stably` (PASSED)
* `test_detect_scale_factor_accepts_exact_100x_tick_but_not_loose_100x` (PASSED)
* `test_bridge_persists_scale_resolution_and_trail_uses_scaled_plan` (PASSED)
* `test_extreme_unresolved_scale_gets_scale_reason_before_market_reason` (PASSED)
* `test_dedupe_key_prefers_origin_signal_id` (PASSED)
* `test_raw_and_approved_count_as_one_signal` (PASSED)
* `test_structural_fallback_groups_raw_and_approved_without_telegram_identity` (PASSED)
* `test_trail_prefers_bridge_scaled_plan_over_raw_payload` (PASSED)
* `test_trail_keeps_operational_terminal_separate_from_entry_mode_posture` (PASSED)
* `test_recent_envelopes_dedupes_raw_and_approved_premium_signal` (PASSED)
* `test_premium_skyai_bad_tick_replay_stays_pending_not_terminal` (PASSED)

---

## 3. Detailed Verification Results

### 3.1 Market-Data-Garbage Protection
* **Outlier Filtering:** The new `app/execution/premium_market_guard.py` checks current spot ticks against the previous valid price. An anomaly exceeding `OUTLIER_MAX_DEVIATION_PCT` (50.0%) is immediately blocked.
* **Non-Terminal Transient Fails:** A single garbage tick (e.g. `101.94` on `SKYAI` when stable is `0.356`) triggers a `premium_market_price_outlier_rejected` / `premium_bad_tick_ignored` event. The signal stays in `WAITING_FOR_ENTRY` state (no execution fill, no SL trigger, no bad PnL).
* **Repeated Failure Hard-stop:** If consecutive bad ticks reach `BAD_TICK_TERMINAL_THRESHOLD` (3), the bridge transitions the signal to a terminal `rejected_scale_review` state with event `premium_terminal_stabilized` (reason: `repeated_bad_market_ticks`), preventing stuck loops.

### 3.2 Scale Lifecycle Transitions
* **Resolution and Persistence:** Envelopes are safely ingested as `scale_unknown=True`. When the bridge tick resolves the factor (e.g., `1e2` or `1e5`), it rescales the target payload in-place, writes `premium_scale_resolved_persisted`, and sets `scale_unknown=False`.
* **API/UI Projection:** `build_trail` in `premium_signal_trail.py` checks bridge history and injects rescaled values (`scaled_entry`, `scaled_stop_loss`, `scaled_targets`) back into the trail entry. The dashboard displays the proper USD values.
* **Extreme Scale Block:** `scale_unresolved_or_bad_price` catches unscaled extreme price ratios (entry/spot ratio > 100 or < 0.01) before standard validation checks run. It terminally rejects them under `premium_scale_unresolved_or_bad_price`.

### 3.3 Raw/Approved Deduplication
* **Identity Mapping:** `app/observability/premium_signal_dedupe.py` maps each premium telegram signal to a deduplication key using priority identifiers (`origin_signal_id` -> `source_uid` -> `message_id` -> `normalized_raw_hash` -> structural profile hash).
* **Structural Profile Fallback:** If telegram IDs are missing, the structural profile creates a key from display symbol, side, entry price, targets, SL, leverage, and a 15-minute time bucket to join raw and approved signals together.
* **API Deduplication:** The recent envelopes API `/signals/envelope/recent` applies `dedupe_premium_signal_records` to prevent raw and approved duplicate rows, reporting only the canonical approved record in the stats.

### 3.4 Trail & Metric Truth
* **Header Hierarchy:** Headline status messages correctly reflect operational terminals (e.g. `ENTRY_DISABLED` or `BRIDGE_REJECTED`) rather than generic posture headers.
* **No Fill, No Success:** Closed states are derived strictly from paper engine fill audits. The dashboard suppresses green PnL alerts when no underlying entry fill exists.
* **Portfolio Separation:** Premium PnL remains attributed to the respective platform source, preventing overlap with autonomous loop metrics.
