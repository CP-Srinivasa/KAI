"""Unit tests for app.learning.bayes_quarantine (DS-20260529-V1 phantom evac)."""

from __future__ import annotations

from app.learning.bayes_quarantine import (
    corruption_reason,
    is_corrupt_close,
    is_quarantined,
    quarantine_reason,
)


def _matic_runaway_close(exit_price: float = 0.408545625) -> dict:
    """A MATIC stale-exit runaway close (the corrupt signature)."""
    return {
        "event_type": "position_closed",
        "symbol": "MATIC/USDT",
        "exit_price": exit_price,
        "trade_pnl_usd": 546.05,
        "fee_usd": 4.2,
        "fill_id": "fill_e8bdbfd3b25e",
    }


def test_matic_runaway_close_is_quarantined() -> None:
    """The exact frozen stale-exit price on MATIC matches the signature."""
    row = _matic_runaway_close()
    assert is_quarantined(row) is True
    assert quarantine_reason(row) == "matic_stale_exit_runaway"


def test_legitimate_matic_close_not_quarantined() -> None:
    """The earlier legitimate MATIC close (2026-05-06, exit ~0.0989) is kept."""
    row = _matic_runaway_close(exit_price=0.09891751650000001)
    assert is_quarantined(row) is False
    assert quarantine_reason(row) is None


def test_other_symbol_at_same_price_not_quarantined() -> None:
    """The signature is symbol-scoped: another symbol at 0.4085 is untouched."""
    row = _matic_runaway_close()
    row["symbol"] = "ADA/USDT"
    assert is_quarantined(row) is False


def test_price_tolerance_is_tight() -> None:
    """A price a hair off the frozen value does NOT match (no over-broad catch)."""
    row = _matic_runaway_close(exit_price=0.408545625 + 1e-6)
    assert is_quarantined(row) is False


def test_missing_or_nonfinite_exit_price_not_quarantined() -> None:
    """Rows without a usable exit price fall through to normal classification."""
    row = _matic_runaway_close()
    row["exit_price"] = None
    assert is_quarantined(row) is False
    row["exit_price"] = "not-a-number"
    assert is_quarantined(row) is False
    del row["exit_price"]
    assert is_quarantined(row) is False


def test_missing_symbol_not_quarantined() -> None:
    row = _matic_runaway_close()
    del row["symbol"]
    assert is_quarantined(row) is False


# --- is_corrupt_close: unified verdict (exact signature OVER generic phantom) ---
# Regression guard for the 2026-06-23 edge-epoch forensic: read-side aggregators
# used ONLY the generic phantom guard (|return|>200%) and so LEAKED the ETH
# off-market signature (+55%, below the cap) into realized PnL. The unified
# verdict layers both so every edge path quarantines the SAME set.


def _eth_off_market_close() -> dict:
    """The ETH off-market signature: +55% (UNDER the 200% phantom cap)."""
    return {
        "event_type": "position_closed",
        "symbol": "ETH/USDT",
        "entry_price": 2100.0,
        "exit_price": 3259.9692,
        "position_side": "long",
        "trade_pnl_usd": 5643.3,
    }


def test_corrupt_close_catches_signature_below_phantom_cap() -> None:
    """ETH off-market (+55%) is missed by the phantom guard but caught by signature."""
    row = _eth_off_market_close()
    # Generic phantom guard alone would MISS this (the leak we are closing):
    from app.execution.phantom_filter import is_phantom_close

    assert is_phantom_close(row["entry_price"], row["exit_price"], row["position_side"]) is False
    # Unified verdict catches it via the exact signature:
    assert is_corrupt_close(row) is True
    assert corruption_reason(row) == "eth_off_market_close"


def test_corrupt_close_catches_generic_phantom_without_signature() -> None:
    """An unsignatured but implausible close (+900%) is caught by the phantom layer."""
    row = {
        "event_type": "position_closed",
        "symbol": "FOO/USDT",
        "entry_price": 1.0,
        "exit_price": 10.0,
        "position_side": "long",
        "trade_pnl_usd": 9.0,
    }
    assert quarantine_reason(row) is None  # no exact signature
    assert is_corrupt_close(row) is True
    assert corruption_reason(row) == "phantom_implied_return"


def test_corrupt_close_catches_matic_runaway() -> None:
    row = _matic_runaway_close()
    row["entry_price"] = 0.088
    assert is_corrupt_close(row) is True
    assert corruption_reason(row) == "matic_stale_exit_runaway"


def test_corrupt_close_keeps_legit_winner_below_cap() -> None:
    """A real +50% winner (no signature, under the cap) is NOT corrupt."""
    row = {
        "event_type": "position_closed",
        "symbol": "SOL/USDT",
        "entry_price": 100.0,
        "exit_price": 150.0,
        "position_side": "long",
        "trade_pnl_usd": 50.0,
    }
    assert is_corrupt_close(row) is False
    assert corruption_reason(row) is None


def test_corrupt_close_conservative_on_missing_prices() -> None:
    """Unverifiable rows (no usable prices, no signature) are never dropped."""
    row = {"event_type": "position_closed", "symbol": "XRP/USDT", "trade_pnl_usd": 1.0}
    assert is_corrupt_close(row) is False
    assert corruption_reason(row) is None
