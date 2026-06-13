"""B-Fix 2026-06-13: exclude pre-leverage (1x-only) premium closes from edge/EV.

Every premium paper trade before the A cutoff (premium.apply_signal_leverage
went live) was sized 1x because the stated leverage was audit-only. Their PnL is
systematically too small and would drag Premium-EV / forward hit-rate / premium-
bonus / re-entry-gate down. This excludes them (premium-only, before cutoff;
audit rows never deleted — skipped + counted).
"""

from __future__ import annotations

from app.observability.edge_report import (
    PREMIUM_LEVERAGE_CUTOFF_UTC,
    parse_closed_trades_with_exclusions,
)

_CUTOFF = "2026-06-13T14:09:49Z"


def _close(
    symbol: str, source: str, ts: str, *, entry: float = 100.0, exit_px: float = 101.0
) -> dict[str, object]:
    return {
        "event_type": "position_closed",
        "symbol": symbol,
        "position_side": "long",
        "entry_price": entry,
        "exit_price": exit_px,
        "quantity": 1.0,
        "reason": "take",
        "trade_pnl_usd": (exit_px - entry),
        "fee_usd": 0.0,
        "timestamp_utc": ts,
        "signal_source": source,
    }


def test_premium_close_before_cutoff_excluded() -> None:
    rows = [_close("COAI/USDT", "telegram_premium_channel_approved", "2026-06-12T12:36:16+00:00")]
    kept, exc = parse_closed_trades_with_exclusions(rows, premium_leverage_cutoff_utc=_CUTOFF)
    assert kept == []
    assert exc.reasons.get("premium_pre_leverage_1x") == 1


def test_premium_close_after_cutoff_kept() -> None:
    rows = [_close("NEW/USDT", "telegram_premium_channel_approved", "2026-06-13T15:00:00+00:00")]
    kept, exc = parse_closed_trades_with_exclusions(rows, premium_leverage_cutoff_utc=_CUTOFF)
    assert len(kept) == 1
    assert "premium_pre_leverage_1x" not in exc.reasons


def test_non_premium_before_cutoff_not_affected() -> None:
    # autonomous / real_analysis were never leverage-distorted → kept
    rows = [
        _close("BTC/USDT", "autonomous_generator", "2026-06-12T10:00:00+00:00"),
        _close("SOL/USDT", "real_analysis", "2026-06-12T10:00:00+00:00"),
    ]
    kept, exc = parse_closed_trades_with_exclusions(rows, premium_leverage_cutoff_utc=_CUTOFF)
    assert len(kept) == 2
    assert "premium_pre_leverage_1x" not in exc.reasons


def test_empty_cutoff_disables_exclusion() -> None:
    rows = [_close("COAI/USDT", "telegram_premium_channel_approved", "2026-06-12T12:36:16+00:00")]
    kept, exc = parse_closed_trades_with_exclusions(rows, premium_leverage_cutoff_utc="")
    assert len(kept) == 1
    assert "premium_pre_leverage_1x" not in exc.reasons


def test_default_cutoff_constant_is_used() -> None:
    # the module default excludes the historical premium era out of the box
    rows = [_close("FIGHT/USDT", "telegram_premium_channel_approved", "2026-06-10T19:17:23+00:00")]
    kept, exc = parse_closed_trades_with_exclusions(rows)  # no explicit cutoff
    assert kept == []
    assert exc.reasons.get("premium_pre_leverage_1x") == 1
    assert PREMIUM_LEVERAGE_CUTOFF_UTC.startswith("2026-06-13")


def test_mixed_batch_only_drops_premium_pre_cutoff() -> None:
    rows = [
        _close("COAI/USDT", "telegram_premium_channel_approved", "2026-06-12T12:00:00+00:00"),
        _close("NEW/USDT", "telegram_premium_channel_approved", "2026-06-13T16:00:00+00:00"),
        _close("BTC/USDT", "autonomous_generator", "2026-06-12T12:00:00+00:00"),
    ]
    kept, exc = parse_closed_trades_with_exclusions(rows, premium_leverage_cutoff_utc=_CUTOFF)
    kept_syms = {t.symbol for t in kept}
    assert kept_syms == {"NEW/USDT", "BTC/USDT"}
    assert exc.reasons.get("premium_pre_leverage_1x") == 1
