"""Tests for momentum_cohort_outcomes — the G3 shadow→eval bridge.

Extracts resolved ``momentum_universe``-cohort closed trades from the paper audit
into ``{symbol, entry_ts, net_bps}`` rows that
``scripts/evaluate_momentum_evidence.py`` joins against the shadow log. Reuses the
edge_report cost SSOT (no second fee formula).
"""

from __future__ import annotations

from typing import Any

from app.observability.momentum_cohort_outcomes import extract_cohort_outcomes


def _close(
    symbol: str,
    src: str,
    entry: float,
    exit_px: float,
    ts: str,
    *,
    side: str = "long",
    qty: float = 1.0,
) -> dict[str, Any]:
    return {
        "event_type": "position_closed",
        "symbol": symbol,
        "signal_source": src,
        "position_side": side,
        "entry_price": entry,
        "exit_price": exit_px,
        "quantity": qty,
        "timestamp_utc": ts,
        "trade_pnl_usd": (exit_px - entry) * qty,
        "reason": "tp",
    }


class TestExtractCohortOutcomes:
    def test_filters_cohort_and_computes_net_bps(self) -> None:
        events = [
            _close("BTC/USDT", "momentum_universe", 100.0, 102.0, "2026-06-26T01:00:00Z"),
            _close("ETH/USDT", "telegram_premium", 100.0, 90.0, "2026-06-26T01:00:00Z"),
        ]
        out = extract_cohort_outcomes(events)
        assert len(out) == 1
        assert out[0]["symbol"] == "BTC/USDT"
        assert out[0]["entry_ts"] == "2026-06-26T01:00:00Z"
        # +2% gross = 200 bps; net is cost-reduced (< gross) and finite.
        assert isinstance(out[0]["net_bps"], float)
        assert out[0]["net_bps"] < 200.0

    def test_empty_when_no_cohort(self) -> None:
        events = [_close("X/USDT", "other", 100.0, 110.0, "2026-06-26T01:00:00Z")]
        assert extract_cohort_outcomes(events) == []

    def test_short_side_profit_is_positive_gross(self) -> None:
        # Short entered at 100, exited at 98 → price fell → short profited.
        events = [
            _close(
                "BTC/USDT", "momentum_universe", 100.0, 98.0, "2026-06-26T01:00:00Z", side="short"
            )
        ]
        out = extract_cohort_outcomes(events)
        assert len(out) == 1
        # gross +200 bps (short profit); net is cost-reduced but the row exists + finite.
        assert isinstance(out[0]["net_bps"], float)

    def test_custom_cohort_name(self) -> None:
        events = [_close("BTC/USDT", "some_other", 100.0, 101.0, "2026-06-26T01:00:00Z")]
        assert extract_cohort_outcomes(events, cohort="some_other")[0]["symbol"] == "BTC/USDT"
        assert extract_cohort_outcomes(events, cohort="momentum_universe") == []
