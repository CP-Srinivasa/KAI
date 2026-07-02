"""Unit tests for V4 Bayes-Posterior-Update.

Covers four layers:
1. classify_outcome — Hit-Definition B with fee-noise inconclusive band.
2. beta_update — conjugate math + inconclusive-exclusion contract.
3. build_posterior_report — lifetime + 90d-rolling, granularity buckets,
   cold-start prior preservation.
4. KAI-no-prediction-rule alignment — outputs are credible intervals, not
   point predictions.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.learning.bayes_posterior import (
    DEFAULT_PRIOR_ALPHA,
    DEFAULT_PRIOR_BETA,
    UNSOURCED_LABEL,
    TradeOutcome,
    beta_update,
    build_posterior_report,
    classify_outcome,
)

# ── classify_outcome ──────────────────────────────────────────────────────


def test_classify_hit_when_pnl_above_fee_band() -> None:
    assert classify_outcome(trade_pnl_usd=50.0, fee_usd=5.0) == "hit"


def test_classify_miss_when_pnl_below_negative_fee_band() -> None:
    assert classify_outcome(trade_pnl_usd=-50.0, fee_usd=5.0) == "miss"


def test_classify_inconclusive_inside_fee_band() -> None:
    """|pnl| ≤ fee → fee-noise zone, no posterior update."""
    assert classify_outcome(trade_pnl_usd=3.0, fee_usd=5.0) == "inconclusive"
    assert classify_outcome(trade_pnl_usd=-4.99, fee_usd=5.0) == "inconclusive"


def test_classify_inconclusive_at_exact_fee_boundary() -> None:
    """pnl == fee → conservative: still inconclusive (band is closed)."""
    assert classify_outcome(trade_pnl_usd=5.0, fee_usd=5.0) == "inconclusive"
    assert classify_outcome(trade_pnl_usd=-5.0, fee_usd=5.0) == "inconclusive"


def test_classify_inconclusive_for_nonfinite_inputs() -> None:
    """A malformed audit row must never move the posterior."""
    assert classify_outcome(trade_pnl_usd=float("nan"), fee_usd=5.0) == "inconclusive"
    assert classify_outcome(trade_pnl_usd=10.0, fee_usd=float("inf")) == "inconclusive"


def test_classify_treats_fee_sign_defensively() -> None:
    """Negative fee (data corruption) — use abs(fee) for the band."""
    assert classify_outcome(trade_pnl_usd=10.0, fee_usd=-5.0) == "hit"
    assert classify_outcome(trade_pnl_usd=-10.0, fee_usd=-5.0) == "miss"


def test_classify_zero_fee_classifies_strictly() -> None:
    """fee=0 → band is degenerate, pnl=0 is still inconclusive (not hit/miss)."""
    assert classify_outcome(trade_pnl_usd=0.0, fee_usd=0.0) == "inconclusive"
    assert classify_outcome(trade_pnl_usd=0.01, fee_usd=0.0) == "hit"
    assert classify_outcome(trade_pnl_usd=-0.01, fee_usd=0.0) == "miss"


# ── beta_update ───────────────────────────────────────────────────────────


def test_beta_update_increments_alpha_on_hit() -> None:
    a, b, h, m, inc = beta_update(2.0, 2.0, ["hit", "hit", "hit"])
    assert a == 5.0 and b == 2.0
    assert h == 3 and m == 0 and inc == 0


def test_beta_update_increments_beta_on_miss() -> None:
    a, b, h, m, inc = beta_update(2.0, 2.0, ["miss", "miss"])
    assert a == 2.0 and b == 4.0
    assert h == 0 and m == 2 and inc == 0


def test_beta_update_inconclusive_does_not_move_posterior() -> None:
    """Inconclusive must NOT update α or β — only the count."""
    a, b, h, m, inc = beta_update(
        2.0, 2.0, ["hit", "inconclusive", "miss", "inconclusive", "inconclusive"]
    )
    assert a == 3.0
    assert b == 3.0
    assert h == 1 and m == 1 and inc == 3


def test_beta_update_empty_returns_prior() -> None:
    a, b, h, m, inc = beta_update(2.0, 2.0, [])
    assert (a, b) == (2.0, 2.0)
    assert (h, m, inc) == (0, 0, 0)


# ── build_posterior_report ────────────────────────────────────────────────


_NOW = datetime(2026, 5, 16, 12, 0, tzinfo=UTC)


def _outcome(
    *,
    fill_id: str,
    symbol: str,
    direction: str,
    outcome: str,
    timestamp: datetime,
    source: str = "telegram_premium_channel_approved",
    trade_pnl: float = 10.0,
    fee: float = 1.0,
    reason: str = "take",
) -> TradeOutcome:
    return TradeOutcome(
        fill_id=fill_id,
        timestamp_utc=timestamp.isoformat(),
        source=source,
        symbol=symbol,
        direction=direction,  # type: ignore[arg-type]
        trade_pnl_usd=trade_pnl,
        fee_usd=fee,
        outcome=outcome,  # type: ignore[arg-type]
        reason=reason,
    )


def test_report_returns_prior_for_empty_input() -> None:
    report = build_posterior_report([], now_utc=_NOW)
    assert report["report_type"] == "bayes_posterior_state"
    assert report["n_buckets"] == 0
    assert report["n_outcomes_total"] == 0
    assert report["buckets"] == {}


def test_report_groups_per_source_symbol_direction() -> None:
    outcomes = [
        _outcome(
            fill_id="f1",
            symbol="BTC/USDT",
            direction="long",
            outcome="hit",
            timestamp=_NOW - timedelta(days=1),
        ),
        _outcome(
            fill_id="f2",
            symbol="BTC/USDT",
            direction="long",
            outcome="miss",
            timestamp=_NOW - timedelta(days=2),
        ),
        _outcome(
            fill_id="f3",
            symbol="ETH/USDT",
            direction="long",
            outcome="hit",
            timestamp=_NOW - timedelta(days=1),
        ),
        _outcome(
            fill_id="f4",
            symbol="BTC/USDT",
            direction="short",
            outcome="hit",
            timestamp=_NOW - timedelta(days=1),
        ),
    ]
    report = build_posterior_report(outcomes, now_utc=_NOW)
    bucket_ids = set(report["buckets"].keys())
    assert "telegram_premium_channel_approved::BTC/USDT::long" in bucket_ids
    assert "telegram_premium_channel_approved::ETH/USDT::long" in bucket_ids
    assert "telegram_premium_channel_approved::BTC/USDT::short" in bucket_ids
    assert len(bucket_ids) == 3


def test_report_lifetime_includes_old_outcomes_rolling_does_not() -> None:
    """Outcome 200 days old → counted in lifetime, NOT in rolling_90d."""
    outcomes = [
        _outcome(
            fill_id="old",
            symbol="BTC/USDT",
            direction="long",
            outcome="hit",
            timestamp=_NOW - timedelta(days=200),
        ),
        _outcome(
            fill_id="new",
            symbol="BTC/USDT",
            direction="long",
            outcome="miss",
            timestamp=_NOW - timedelta(days=5),
        ),
    ]
    report = build_posterior_report(outcomes, now_utc=_NOW)
    bucket = report["buckets"]["telegram_premium_channel_approved::BTC/USDT::long"]
    assert bucket["lifetime"]["n"] == 2  # both
    assert bucket["lifetime"]["hits"] == 1
    assert bucket["lifetime"]["miss"] == 1
    assert bucket["rolling_90d"]["n"] == 1  # only the recent miss
    assert bucket["rolling_90d"]["hits"] == 0
    assert bucket["rolling_90d"]["miss"] == 1


def test_report_uses_default_prior_for_cold_start() -> None:
    """Beta(2,2) prior → posterior_mean = 0.5 with zero observations.

    A bucket that appears ONLY in rolling (not lifetime) still receives
    the prior — and a bucket with only inconclusive outcomes effectively
    stays at the prior. Verified via posterior_mean == 0.5.
    """
    outcomes = [
        _outcome(
            fill_id="incOnly",
            symbol="BTC/USDT",
            direction="long",
            outcome="inconclusive",
            timestamp=_NOW - timedelta(days=1),
        ),
    ]
    report = build_posterior_report(outcomes, now_utc=_NOW)
    bucket = report["buckets"]["telegram_premium_channel_approved::BTC/USDT::long"]
    assert bucket["lifetime"]["hits"] == 0
    assert bucket["lifetime"]["miss"] == 0
    assert bucket["lifetime"]["inconclusive"] == 1
    # Pure prior with no observed updates → 2/(2+2) = 0.5
    assert bucket["lifetime"]["alpha"] == DEFAULT_PRIOR_ALPHA
    assert bucket["lifetime"]["beta"] == DEFAULT_PRIOR_BETA
    assert bucket["lifetime"]["posterior_mean"] == pytest.approx(0.5)


def test_report_unsourced_outcomes_land_in_dedicated_bucket() -> None:
    """tradingloop-source label must remain separate from premium-channel buckets."""
    outcomes = [
        _outcome(
            fill_id="anon",
            symbol="BTC/USDT",
            direction="long",
            outcome="hit",
            timestamp=_NOW - timedelta(days=1),
            source=UNSOURCED_LABEL,
        ),
    ]
    report = build_posterior_report(outcomes, now_utc=_NOW)
    assert f"{UNSOURCED_LABEL}::BTC/USDT::long" in report["buckets"]


def test_report_credible_interval_widens_with_few_observations() -> None:
    """Bucket with n=3 has wider CI than bucket with n=30 at same hit-rate."""
    n_small = 3
    n_big = 30
    outcomes_small = [
        _outcome(
            fill_id=f"s{i}",
            symbol="BTC/USDT",
            direction="long",
            outcome="hit",
            timestamp=_NOW - timedelta(days=1),
        )
        for i in range(n_small)
    ]
    outcomes_big = [
        _outcome(
            fill_id=f"b{i}",
            symbol="ETH/USDT",
            direction="long",
            outcome="hit",
            timestamp=_NOW - timedelta(days=1),
        )
        for i in range(n_big)
    ]
    report = build_posterior_report(outcomes_small + outcomes_big, now_utc=_NOW)
    small_bucket = report["buckets"]["telegram_premium_channel_approved::BTC/USDT::long"]
    big_bucket = report["buckets"]["telegram_premium_channel_approved::ETH/USDT::long"]
    small_width = (
        small_bucket["lifetime"]["wilson_upper_95"] - small_bucket["lifetime"]["wilson_lower_95"]
    )
    big_width = (
        big_bucket["lifetime"]["wilson_upper_95"] - big_bucket["lifetime"]["wilson_lower_95"]
    )
    assert small_width > big_width, (
        f"smaller n should yield wider CI; got small={small_width} big={big_width}"
    )


def test_report_config_records_decisions() -> None:
    """The output must self-describe its hit-definition + decay choice."""
    report = build_posterior_report([], now_utc=_NOW)
    cfg = report["config"]
    assert cfg["hit_definition"] == "paper_pnl_after_fee"
    assert cfg["break_even_treatment"] == "inconclusive_fee_band"
    assert cfg["rolling_window_days"] == 90
    assert cfg["prior_alpha"] == DEFAULT_PRIOR_ALPHA
    assert cfg["prior_beta"] == DEFAULT_PRIOR_BETA


def test_report_lifetime_vs_rolling_divergence_signals_regime_drift() -> None:
    """A source that USED to hit 80% but flipped to 20% recently:
    lifetime stays high, rolling tanks. This contrast is the regime-drift signal."""
    # 20 old hits, then 10 recent misses
    outcomes: list[TradeOutcome] = []
    for i in range(20):
        outcomes.append(
            _outcome(
                fill_id=f"old_hit{i}",
                symbol="BTC/USDT",
                direction="long",
                outcome="hit",
                timestamp=_NOW - timedelta(days=120 + i),  # all outside 90d window
            )
        )
    for i in range(10):
        outcomes.append(
            _outcome(
                fill_id=f"new_miss{i}",
                symbol="BTC/USDT",
                direction="long",
                outcome="miss",
                timestamp=_NOW - timedelta(days=10),  # inside 90d
            )
        )
    report = build_posterior_report(outcomes, now_utc=_NOW)
    bucket = report["buckets"]["telegram_premium_channel_approved::BTC/USDT::long"]
    lt = bucket["lifetime"]
    rl = bucket["rolling_90d"]
    # Lifetime: 20 hits + 10 miss → α=22, β=12, mean ≈ 0.647
    assert lt["hits"] == 20 and lt["miss"] == 10
    assert lt["posterior_mean"] > 0.5
    # Rolling: only 10 miss → α=2, β=12, mean ≈ 0.143
    assert rl["hits"] == 0 and rl["miss"] == 10
    assert rl["posterior_mean"] < 0.2
    # The drift is visible: lifetime mean > rolling mean by > 0.4
    assert lt["posterior_mean"] - rl["posterior_mean"] > 0.4
