"""Exchange preflight — tick/step/notional/percent-price validation + normalization.

Grounded in env ENV-TG-001275462917-23879-502ef70a: US/USDT targets
0.008415/0.008455/0.008495 are off-grid for a 0.00001 tick.
"""

from __future__ import annotations

from decimal import Decimal

from app.execution.exchange_preflight import (
    SymbolFilters,
    is_on_grid,
    preflight_order,
)
from app.risk.reason_codes import RejectCode

# tickSize 0.00001 per the Binance US/USDT launch announcement.
US_FILTERS = SymbolFilters(
    symbol="USUSDT",
    tick_size=Decimal("0.00001"),
    step_size=Decimal("1"),
    min_qty=Decimal("1"),
    min_notional=Decimal("5"),
)


def test_on_grid_helper() -> None:
    assert is_on_grid(Decimal("0.00837"), Decimal("0.00001")) is True
    assert is_on_grid(Decimal("0.008415"), Decimal("0.00001")) is False


def test_us_targets_off_grid_rejected_without_normalization() -> None:
    res = preflight_order(
        filters=US_FILTERS,
        side="buy",
        entry_price=0.00833,
        stop_loss=0.00798,
        targets=[0.00837, 0.008415, 0.008455, 0.008495],
        allow_normalization=False,
    )
    assert res.ok is False
    assert res.reason_code == RejectCode.INVALID_TICK_SIZE.value
    # the three half-tick targets are flagged; entry/sl/first target are on grid
    offgrid = [v for v in res.violations if "take_profit" in v]
    assert len(offgrid) == 3


def test_us_targets_normalized_within_tolerance() -> None:
    res = preflight_order(
        filters=US_FILTERS,
        side="buy",
        entry_price=0.00833,
        stop_loss=0.00798,
        targets=[0.00837, 0.008415, 0.008455, 0.008495],
        allow_normalization=True,
        tolerance_pct=0.1,
    )
    assert res.ok is True
    # long take-profit snaps UP (harder to reach) — conservative
    assert res.normalized_targets[1] == 0.00842  # 0.008415 -> up
    assert res.normalized_targets[2] == 0.00846
    assert res.normalized_targets[3] == 0.0085
    # every snap recorded
    tp_adj = [a for a in res.adjustments if a.field_name == "take_profit"]
    assert len(tp_adj) == 3
    assert all(a.rounding_direction == "up" for a in tp_adj)
    assert all(a.risk_impact == "harder_to_reach" for a in tp_adj)


def test_stop_loss_snaps_conservatively_toward_entry() -> None:
    # long SL below entry, off grid -> rounds UP (tighter stop).
    filt = SymbolFilters(symbol="X", tick_size=Decimal("0.001"))
    res = preflight_order(
        filters=filt,
        side="buy",
        entry_price=1.0,
        stop_loss=0.9985,  # off-grid
        allow_normalization=True,
        tolerance_pct=1.0,
    )
    assert res.ok is True
    sl_adj = [a for a in res.adjustments if a.field_name == "stop_loss"][0]
    assert sl_adj.rounding_direction == "up"
    assert res.normalized_stop_loss == 0.999  # closer to entry than 0.9985
    assert sl_adj.risk_impact == "tighter"


def test_snap_exceeding_tolerance_rejects() -> None:
    filt = SymbolFilters(symbol="X", tick_size=Decimal("0.01"))
    res = preflight_order(
        filters=filt,
        side="buy",
        entry_price=1.005,  # 0.5% from nearest grid 1.00/1.01 -> exceeds 0.1% tol
        allow_normalization=True,
        tolerance_pct=0.1,
    )
    assert res.ok is False
    assert res.reason_code == RejectCode.INVALID_TICK_SIZE.value


def test_min_notional_violation() -> None:
    res = preflight_order(
        filters=US_FILTERS,
        side="buy",
        entry_price=0.00833,
        quantity=100,  # 100 * 0.00833 = 0.833 < min_notional 5
    )
    assert res.ok is False
    assert res.reason_code == RejectCode.EXCHANGE_FILTER.value
    assert any("notional_below_min" in v for v in res.violations)


def test_qty_snaps_down_to_step() -> None:
    res = preflight_order(
        filters=US_FILTERS,
        side="buy",
        entry_price=0.00833,
        quantity=1234.7,
        allow_normalization=True,
    )
    assert res.normalized_qty == 1234.0
    qty_adj = [a for a in res.adjustments if a.field_name == "quantity"][0]
    assert qty_adj.rounding_direction == "down"


def test_percent_price_band() -> None:
    filt = SymbolFilters(
        symbol="X",
        tick_size=Decimal("0.01"),
        percent_price_up=Decimal("1.05"),
        percent_price_down=Decimal("0.95"),
    )
    # entry 1.20 vs reference 1.00 -> above 1.05 band
    res = preflight_order(filters=filt, side="buy", entry_price=1.20, reference_price=1.00)
    assert res.ok is False
    assert any("percent_price_exceeded" in v for v in res.violations)


def test_leverage_bracket() -> None:
    filt = SymbolFilters(symbol="X", tick_size=Decimal("0.01"), max_leverage=Decimal("20"))
    res = preflight_order(filters=filt, side="buy", entry_price=1.00, leverage=50)
    assert res.ok is False
    assert any("leverage_exceeds_max" in v for v in res.violations)


def test_non_trading_status_rejected() -> None:
    filt = SymbolFilters(symbol="X", tick_size=Decimal("0.01"), status="BREAK")
    res = preflight_order(filters=filt, side="buy", entry_price=1.00)
    assert res.ok is False
    assert res.reason_code == RejectCode.EXCHANGE_FILTER.value


def test_binance_adapter_parses_filters() -> None:
    info = {
        "symbol": "USUSDT",
        "status": "TRADING",
        "filters": [
            {"filterType": "PRICE_FILTER", "tickSize": "0.00001"},
            {"filterType": "LOT_SIZE", "stepSize": "1", "minQty": "1", "maxQty": "1000000"},
            {"filterType": "MIN_NOTIONAL", "notional": "5"},
            {"filterType": "PERCENT_PRICE", "multiplierUp": "1.05", "multiplierDown": "0.95"},
        ],
    }
    f = SymbolFilters.from_binance_futures(info)
    assert f.tick_size == Decimal("0.00001")
    assert f.step_size == Decimal("1")
    assert f.min_notional == Decimal("5")
    assert f.percent_price_up == Decimal("1.05")


def test_bybit_adapter_parses_filters() -> None:
    info = {
        "symbol": "USUSDT",
        "status": "Trading",
        "priceFilter": {"tickSize": "0.00001"},
        "lotSizeFilter": {"qtyStep": "1", "minOrderQty": "1", "maxOrderQty": "500000"},
        "leverageFilter": {"maxLeverage": "40"},
    }
    f = SymbolFilters.from_bybit_instrument(info)
    assert f.tick_size == Decimal("0.00001")
    assert f.max_leverage == Decimal("40")
    # bybit "Trading" must be accepted as tradeable
    res = preflight_order(filters=f, side="buy", entry_price=0.00833)
    assert res.ok is True
