"""Unit tests for the diversification overview service (CLI/API read surface)."""

from __future__ import annotations

from app.execution.portfolio_read import (
    ExposureSummary,
    PortfolioSnapshot,
    PositionSummary,
)
from app.trading.diversification_service import (
    build_diversification_overview_from_snapshot,
)


def _pos(symbol: str, qty: float, entry: float, source: str = "") -> PositionSummary:
    return PositionSummary(
        symbol=symbol,
        quantity=qty,
        avg_entry_price=entry,
        stop_loss=None,
        take_profit=None,
        market_price=entry,
        market_value_usd=qty * entry,
        unrealized_pnl_usd=0.0,
        provider="test",
        market_data_retrieved_at_utc=None,
        market_data_source_timestamp_utc=None,
        market_data_is_stale=False,
        market_data_freshness_seconds=1.0,
        market_data_available=True,
        source=source,
    )


def _snapshot(positions: tuple[PositionSummary, ...]) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        generated_at_utc="t",
        source="test",
        audit_path="x",
        cash_usd=1000.0,
        realized_pnl_usd=0.0,
        total_market_value_usd=sum(p.market_value_usd or 0.0 for p in positions),
        total_equity_usd=1000.0 + sum(p.market_value_usd or 0.0 for p in positions),
        position_count=len(positions),
        positions=positions,
        exposure_summary=ExposureSummary(
            priced_position_count=len(positions),
            stale_position_count=0,
            unavailable_price_count=0,
            gross_exposure_usd=sum(p.market_value_usd or 0.0 for p in positions),
            net_exposure_usd=sum(p.market_value_usd or 0.0 for p in positions),
            largest_position_symbol=positions[0].symbol if positions else None,
            largest_position_weight_pct=None,
            mark_to_market_status="ok",
        ),
        available=True,
    )


def test_overview_has_all_sections() -> None:
    snap = _snapshot(
        (
            _pos("BTC/USDT", 0.1, 60000, source="cron"),
            _pos("ETH/USDT", 2, 3000, source="cron"),
            _pos("SOL/USDT", 5, 150, source="premium_telegram"),
        )
    )
    ov = build_diversification_overview_from_snapshot(snap)
    assert ov["report_type"] == "diversification_overview"
    assert "concentration" in ov
    assert "asset_distribution" in ov
    assert "candidates" in ov
    assert "by_source" in ov
    assert "cluster_warnings" in ov


def test_overview_flags_btc_eth_dominance() -> None:
    snap = _snapshot(
        (
            _pos("BTC/USDT", 0.1, 60000, source="cron"),
            _pos("ETH/USDT", 2, 3000, source="cron"),
        )
    )
    ov = build_diversification_overview_from_snapshot(snap)
    conc = ov["concentration"]
    assert conc["btc_eth_short_term_pct"] is not None
    assert conc["btc_eth_short_term_pct"] > 90
    assert ov["cluster_warnings"]


def test_overview_by_source_attribution() -> None:
    snap = _snapshot(
        (
            _pos("BTC/USDT", 0.1, 60000, source="cron"),
            _pos("SOL/USDT", 5, 150, source="premium_telegram"),
        )
    )
    ov = build_diversification_overview_from_snapshot(snap)
    sources = {row["source"]: row for row in ov["by_source"]}
    assert "cron" in sources
    assert "premium_telegram" in sources
    assert sources["cron"]["exposure_usd"] > sources["premium_telegram"]["exposure_usd"]


def test_overview_candidates_broaden_beyond_btc_eth() -> None:
    snap = _snapshot((_pos("BTC/USDT", 0.1, 60000, source="cron"),))
    ov = build_diversification_overview_from_snapshot(snap)
    picked = [c["symbol"] for c in ov["candidates"] if c["included"]]
    bases = {s.split("/")[0] for s in picked}
    assert bases - {"BTC", "ETH"}


def test_overview_empty_portfolio_does_not_crash() -> None:
    snap = _snapshot(())
    ov = build_diversification_overview_from_snapshot(snap)
    assert ov["concentration"]["evaluable"] is False
    # candidates still computed from the universe even with an empty book
    assert ov["candidates"]
