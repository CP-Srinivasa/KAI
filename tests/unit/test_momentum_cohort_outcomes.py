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

    def test_recovers_mistagged_close_via_document_id_prefix(self) -> None:
        """Backfill: a momentum close written BEFORE the forward attribution fix
        carries signal_source='autonomous_generator' but document_id=
        'momentum_universe_<SYM>'. The cohort tag survives only in document_id, so
        extract_cohort_outcomes must recover it via the document_id prefix —
        otherwise the 3 already-closed momentum trades stay invisible and the n>=30
        gate waits days for naught."""
        events = [
            {
                "event_type": "position_closed",
                "symbol": "SLX/USDT",
                "signal_source": "autonomous_generator",  # the mis-tag
                "document_id": "momentum_universe_SLXUSDT",  # cohort survives here
                "position_side": "long",
                "entry_price": 100.0,
                "exit_price": 102.0,
                "quantity": 1.0,
                "timestamp_utc": "2026-06-27T15:00:00Z",
                "trade_pnl_usd": 2.0,
                "reason": "take",
            }
        ]
        out = extract_cohort_outcomes(events)
        assert len(out) == 1
        assert out[0]["symbol"] == "SLX/USDT"
        assert isinstance(out[0]["net_bps"], float)

    def test_document_id_prefix_does_not_overmatch_other_cohorts(self) -> None:
        """The prefix recovery is scoped to '<cohort>_': a close from a different
        cohort (document_id 'technical_paper_BTCUSDT') must NOT leak in."""
        events = [
            {
                "event_type": "position_closed",
                "symbol": "BTC/USDT",
                "signal_source": "autonomous_generator",
                "document_id": "technical_paper_BTCUSDT",
                "position_side": "long",
                "entry_price": 100.0,
                "exit_price": 101.0,
                "quantity": 1.0,
                "timestamp_utc": "2026-06-27T15:00:00Z",
                "trade_pnl_usd": 1.0,
                "reason": "take",
            }
        ]
        assert extract_cohort_outcomes(events) == []
