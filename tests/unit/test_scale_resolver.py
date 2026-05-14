"""Unit tests for app.execution.scale_resolver (P1 #8)."""

from __future__ import annotations

import pytest

from app.execution import scale_resolver as sr


# ── detect_scale_factor ─────────────────────────────────────────────────────


def test_detect_returns_1_for_usd_scale_signals():
    # BTC at 60000 with channel value 60000 → no rescale
    assert sr.detect_scale_factor(60000.0, 60000.0) == 1.0
    # ETH at 3500 with channel value 3500 → no rescale
    assert sr.detect_scale_factor(3500.0, 3500.0) == 1.0


def test_detect_returns_1e8_for_swarms_pattern():
    # SWARMS 32450 → $0.0003245 → factor 1e8 (32450/0.0003245 ≈ 1e8)
    factor = sr.detect_scale_factor(32450.0, 0.0003245)
    assert factor == 1e8


def test_detect_returns_1e6_for_mid_range_pattern():
    # Hypothetical mid-range: channel value 1500 → $0.0015 → factor 1e6
    factor = sr.detect_scale_factor(1500.0, 0.0015)
    assert factor == 1e6


def test_detect_returns_1_when_ratio_outside_tolerance():
    # 2× drift is real volatility, not a scale ladder
    assert sr.detect_scale_factor(120.0, 60.0) == 1.0


def test_detect_returns_1_for_non_positive_inputs():
    assert sr.detect_scale_factor(0.0, 100.0) == 1.0
    assert sr.detect_scale_factor(100.0, 0.0) == 1.0
    assert sr.detect_scale_factor(-1.0, 100.0) == 1.0


# ── apply_scale_to_payload ──────────────────────────────────────────────────


def test_apply_scale_noop_when_factor_one():
    payload = {"entry_value": 100.0, "stop_loss": 95.0, "targets": [101.0]}
    sr.apply_scale_to_payload(payload, 1.0)
    assert payload == {"entry_value": 100.0, "stop_loss": 95.0, "targets": [101.0]}


def test_apply_scale_divides_entry_sl_targets():
    payload = {
        "entry_value": 32450.0,
        "entry_min": 32000.0,
        "entry_max": 33000.0,
        "stop_loss": 31000.0,
        "targets": [33000.0, 34000.0],
    }
    sr.apply_scale_to_payload(payload, 1e6)
    assert payload["entry_value"] == pytest.approx(32450.0 / 1e6, rel=1e-9)
    assert payload["entry_min"] == pytest.approx(32000.0 / 1e6, rel=1e-9)
    assert payload["entry_max"] == pytest.approx(33000.0 / 1e6, rel=1e-9)
    assert payload["stop_loss"] == pytest.approx(31000.0 / 1e6, rel=1e-9)
    assert payload["targets"] == [pytest.approx(33000.0 / 1e6), pytest.approx(34000.0 / 1e6)]


def test_apply_scale_skips_non_numeric_targets():
    payload = {"entry_value": 100.0, "targets": [50.0, "garbage", None, 60.0]}
    sr.apply_scale_to_payload(payload, 1e3)
    assert payload["targets"] == [0.05, 0.06]


def test_apply_scale_skips_missing_fields():
    payload: dict[str, object] = {}
    sr.apply_scale_to_payload(payload, 1e3)
    assert payload == {}


# ── resolve_scale_for_symbol ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_returns_none_when_fetcher_returns_none():
    async def _no_price(symbol: str) -> float | None:
        return None

    factor = await sr.resolve_scale_for_symbol("UNKNOWN/USDT", 100.0, price_fetcher=_no_price)
    assert factor is None


@pytest.mark.asyncio
async def test_resolve_returns_1_when_already_usd_scale():
    async def _btc_price(symbol: str) -> float | None:
        return 60000.0

    factor = await sr.resolve_scale_for_symbol("BTC/USDT", 60000.0, price_fetcher=_btc_price)
    assert factor == 1.0


@pytest.mark.asyncio
async def test_resolve_returns_1e8_for_integer_tick_pattern():
    async def _swarms_price(symbol: str) -> float | None:
        return 0.0003245

    factor = await sr.resolve_scale_for_symbol("SWARMS/USDT", 32450.0, price_fetcher=_swarms_price)
    assert factor == 1e8
