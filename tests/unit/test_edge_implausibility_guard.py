"""Generic off-market implausibility guard (Goal 2026-06-01 B) + ETH signature (A).

Forensics 2026-06-01: a single ETH close at exit $3259.9692 (entry ~$2100, +55%)
inflated the edge aggregate while real ETH traded $1960-$2100. B = a symmetric
``|exit/entry-1| > threshold`` guard in edge_report that drops such off-market
prints from the cost-adjusted edge; A = the same close recorded as a forensic
signature in bayes_quarantine (incident DS-20260601-EDGE-OUTLIER).

Behaviour under test (not implementation):
- the +55% off-market close is excluded (via signature AND/OR guard);
- a plausible loser (-15%) is NEVER excluded (guard must not scrub losses);
- the guard is symmetric (a -50% corrupt print is excluded too);
- threshold=0 disables the generic guard (signatures still apply);
- a signature match is counted once, not double-counted by the guard.
"""

from __future__ import annotations

from app.learning.bayes_quarantine import is_quarantined, quarantine_reason
from app.observability.edge_report import parse_closed_trades_with_exclusions


def _close(symbol: str, entry: float, exit_px: float, *, side: str = "long") -> dict[str, object]:
    return {
        "event_type": "position_closed",
        "symbol": symbol,
        "position_side": side,
        "entry_price": entry,
        "exit_price": exit_px,
        "quantity": 1.0,
        "reason": "take",
        "trade_pnl_usd": (exit_px - entry),
        "fee_usd": 0.0,
        "timestamp_utc": "2026-06-01T12:00:00+00:00",
    }


class TestEthSignature:
    def test_eth_off_market_close_is_quarantined(self) -> None:
        row = _close("ETH/USDT", 2100.358657874716, 3259.9692)
        assert is_quarantined(row) is True
        assert quarantine_reason(row) == "eth_off_market_close"

    def test_normal_eth_close_is_not_quarantined(self) -> None:
        assert is_quarantined(_close("ETH/USDT", 2000.0, 2015.0)) is False

    def test_matic_signature_still_present(self) -> None:
        assert quarantine_reason(_close("MATIC/USDT", 0.0876, 0.408545625)) == (
            "matic_stale_exit_runaway"
        )


class TestImplausibilityGuard:
    def test_off_market_winner_excluded_by_guard(self) -> None:
        # +50% move on a symbol with no signature -> generic guard drops it.
        trades, excl = parse_closed_trades_with_exclusions([_close("SOL/USDT", 100.0, 151.0)])
        assert trades == []
        assert excl.excluded_count == 1
        assert "implausible_move_gt_40pct" in excl.reasons

    def test_plausible_loser_is_kept(self) -> None:
        # -15% is a plausible adverse move, NOT corruption — must survive.
        trades, excl = parse_closed_trades_with_exclusions([_close("BTC/USDT", 76865.0, 65485.0)])
        assert len(trades) == 1
        assert excl.excluded_count == 0

    def test_guard_is_symmetric_on_corrupt_loss(self) -> None:
        # a -50% off-market print is excluded just like a +50% one.
        trades, excl = parse_closed_trades_with_exclusions([_close("SOL/USDT", 100.0, 50.0)])
        assert trades == []
        assert excl.excluded_count == 1

    def test_threshold_zero_disables_generic_guard(self) -> None:
        trades, excl = parse_closed_trades_with_exclusions(
            [_close("SOL/USDT", 100.0, 151.0)], implausible_move_threshold=0.0
        )
        assert len(trades) == 1
        assert excl.excluded_count == 0

    def test_signature_match_not_double_counted_by_guard(self) -> None:
        # the ETH +55% close matches the signature AND would trip the guard;
        # it must be counted exactly once, under the signature reason.
        trades, excl = parse_closed_trades_with_exclusions(
            [_close("ETH/USDT", 2100.358657874716, 3259.9692)]
        )
        assert trades == []
        assert excl.excluded_count == 1
        assert excl.reasons.get("eth_off_market_close") == 1
        assert "implausible_move_gt_40pct" not in excl.reasons

    def test_normal_trade_passes_both_layers(self) -> None:
        trades, excl = parse_closed_trades_with_exclusions([_close("ETH/USDT", 2000.0, 2015.0)])
        assert len(trades) == 1
        assert excl.excluded_count == 0
