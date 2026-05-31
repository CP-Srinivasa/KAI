"""Unit tests for app.learning.bayes_quarantine (DS-20260529-V1 phantom evac)."""

from __future__ import annotations

from app.learning.bayes_quarantine import is_quarantined, quarantine_reason


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
