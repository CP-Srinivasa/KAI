"""Tests for deliverable 4: bayes_quarantine recognises quarantine_off_venue_unpriceable.

TDD: written before the implementation.
"""

from __future__ import annotations

from app.learning.bayes_quarantine import corruption_reason, is_corrupt_close


def _offvenue_close(symbol: str = "SLX/USDT") -> dict:
    """A position_closed row written by the remediation script."""
    return {
        "schema_version": "v2",
        "event_type": "position_closed",
        "symbol": symbol,
        "reason": "quarantine_off_venue_unpriceable",
        "quantity": 10.0,
        "entry_price": 0.05,
        "exit_price": 0.05,  # flat-close
        "fill_id": "quarantine_fill_SLX_USDT",
        "order_id": "quarantine_order_SLX_USDT",
        "realized_pnl_usd": -1.23,
        "trade_pnl_usd": 0.0,
        "fee_usd": 0.0,
        "position_side": "long",
    }


def test_offvenue_close_is_corrupt() -> None:
    """A close with reason=quarantine_off_venue_unpriceable is classified corrupt."""
    row = _offvenue_close()
    assert is_corrupt_close(row) is True


def test_offvenue_close_corruption_reason_string() -> None:
    """corruption_reason returns the quarantine reason string (not None)."""
    row = _offvenue_close()
    assert corruption_reason(row) == "quarantine_off_venue_unpriceable"


def test_offvenue_close_different_symbols_still_corrupt() -> None:
    """The reason-field check is symbol-agnostic."""
    for sym in ("ACT/USDT", "O/USDT", "SLX/USDT"):
        row = _offvenue_close(sym)
        assert is_corrupt_close(row) is True, f"Expected corrupt for {sym}"


def test_normal_close_with_other_reason_not_corrupt() -> None:
    """A normal close (reason=sl) is not classified corrupt by the new check."""
    row = {
        "event_type": "position_closed",
        "symbol": "BTC/USDT",
        "reason": "sl",
        "entry_price": 50_000.0,
        "exit_price": 49_000.0,
        "position_side": "long",
        "trade_pnl_usd": -1000.0,
    }
    assert is_corrupt_close(row) is False


def test_close_without_reason_field_not_corrupt() -> None:
    """A close row with no 'reason' field is not corrupted by this check."""
    row = {
        "event_type": "position_closed",
        "symbol": "SOL/USDT",
        "entry_price": 100.0,
        "exit_price": 95.0,
        "position_side": "long",
        "trade_pnl_usd": -5.0,
    }
    assert is_corrupt_close(row) is False


def test_offvenue_close_reason_takes_priority_over_phantom_guard() -> None:
    """The off-venue reason is checked before the phantom guard — not double-counted."""
    row = _offvenue_close()
    # reason="quarantine_off_venue_unpriceable" with entry==exit → not a phantom.
    # The corruption reason must be the quarantine string, not "phantom_implied_return".
    reason = corruption_reason(row)
    assert reason == "quarantine_off_venue_unpriceable"
