"""Sprint B regression (NEO-F-302): open-fill fees must NOT be netted against
closed round-trip PnL.

The real Operator bug (2026-06-01): gross was reported as +433 instead of -283
because a naive 'sum(fee_usd over ALL fills) - sum(closed trade_pnl)' folded the
entry-fill fees of still-OPEN positions into the closed-trade accounting.

A fill's `fee_usd` is charged per fill (entry AND exit). A closed round-trip's
`trade_pnl_usd` is already NET of both legs' fees. So:

  closed_net_pnl   := sum(trade_pnl_usd of CLOSED round-trips)        [already net]
  fees_closed      := entry_fee + exit_fee of CLOSED round-trips      [informational]
  fees_open        := entry_fee of OPEN positions (no exit yet)       [separate bucket]

These three are distinct. The forbidden operation is mixing fees_open into the
closed accounting. This test pins the separation so the bug cannot recur.
"""

from __future__ import annotations

import pytest

from app.execution.cost_model import FillRecord, summarize_fees


def _entry(symbol: str, fee: float, pnl: float = 0.0) -> FillRecord:
    # entry leg: side=buy, fee charged, no realized pnl yet
    return FillRecord(symbol=symbol, leg="entry", fee_usd=fee, trade_pnl_usd=pnl)


def _exit(symbol: str, fee: float, pnl: float) -> FillRecord:
    # exit leg of a CLOSED round-trip: trade_pnl_usd is NET of both legs' fees
    return FillRecord(symbol=symbol, leg="exit", fee_usd=fee, trade_pnl_usd=pnl)


def test_open_entry_fees_not_folded_into_closed_pnl():
    """Two closed round-trips net -283; three open entries paid fees too. The
    closed net PnL must stay -283 — open fees go to their own bucket."""
    fills = [
        # closed round-trip A: net -150 (already fee-net)
        _entry("BTC/USDT", fee=10.0),
        _exit("BTC/USDT", fee=10.0, pnl=-150.0),
        # closed round-trip B: net -133
        _entry("ETH/USDT", fee=8.0),
        _exit("ETH/USDT", fee=8.0, pnl=-133.0),
        # three OPEN positions — only entry fees paid, no exit yet
        _entry("SOL/USDT", fee=12.0),
        _entry("XRP/USDT", fee=5.0),
        _entry("ADA/USDT", fee=7.0),
    ]
    summary = summarize_fees(fills)

    assert summary.closed_net_pnl_usd == pytest.approx(-283.0)
    # fees_closed = both legs of A and B = 10+10+8+8 = 36
    assert summary.fees_closed_usd == pytest.approx(36.0)
    # fees_open = entry fees of the three still-open positions = 12+5+7 = 24
    assert summary.fees_open_usd == pytest.approx(24.0)


def test_naive_all_fees_minus_closed_pnl_is_flagged_wrong():
    """The forbidden number: sum(ALL fee_usd) treated as if it belonged to the
    closed book. summarize_fees must NOT expose that as closed accounting; the
    open bucket is what keeps it honest."""
    fills = [
        _entry("BTC/USDT", fee=10.0),
        _exit("BTC/USDT", fee=10.0, pnl=-283.0),
        _entry("SOL/USDT", fee=716.0),  # exaggerated open fee to dramatize the bug
    ]
    summary = summarize_fees(fills)

    # The naive (wrong) computation a human might do:
    naive_gross = -(summary.closed_net_pnl_usd) - summary.total_fees_usd
    # naive_gross would be 283 - 736 ... the point: closed_net is isolated.
    assert summary.closed_net_pnl_usd == pytest.approx(-283.0)
    # open fee is NOT part of the closed book
    assert summary.fees_open_usd == pytest.approx(716.0)
    assert summary.fees_closed_usd == pytest.approx(20.0)
    # total is informational only and explicitly the sum of the two buckets
    assert summary.total_fees_usd == pytest.approx(summary.fees_open_usd + summary.fees_closed_usd)
    # guard: the naive figure is demonstrably different from the honest closed net
    assert naive_gross != pytest.approx(summary.closed_net_pnl_usd)


def test_empty_book_is_all_zero():
    summary = summarize_fees([])
    assert summary.closed_net_pnl_usd == 0.0
    assert summary.fees_open_usd == 0.0
    assert summary.fees_closed_usd == 0.0
    assert summary.total_fees_usd == 0.0


def test_gross_winners_among_closed_are_preserved():
    """4/22 gross winners must survive: a closed positive trade keeps its sign,
    open fees never erode it."""
    fills = [
        _entry("BTC/USDT", fee=10.0),
        _exit("BTC/USDT", fee=10.0, pnl=+120.0),  # a winner (net)
        _entry("ETH/USDT", fee=10.0),  # open, fee paid
    ]
    summary = summarize_fees(fills)
    assert summary.closed_net_pnl_usd == pytest.approx(120.0)
    assert summary.fees_open_usd == pytest.approx(10.0)
