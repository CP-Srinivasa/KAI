"""Tests for the CoinGecko market-overview graduation (G1, Source-Intake §9).

Covers:
- CoinGeckoAdapter.get_market_overview parse (success / None-tolerant / fail-closed)
- CoinGeckoOverviewSettings default-off contract + symbols CSV parsing
- CoinGeckoOverviewStore atomic write/read roundtrip + corruption tolerance
- append_overview_shadow_log append + readability
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.market_data.coingecko_adapter import CoinGeckoAdapter
from app.market_data.coingecko_overview import (
    CoinGeckoMarketOverview,
    CoinGeckoOverviewSettings,
    CoinGeckoOverviewStore,
    append_overview_shadow_log,
)


def _adapter() -> CoinGeckoAdapter:
    return CoinGeckoAdapter(freshness_threshold_seconds=120.0, timeout_seconds=5)


def _markets_response(
    *,
    rank: object = 1,
    market_cap: object = 1_300_000_000_000.0,
    change_30d: object = 8.5,
    last_updated: str = "2026-06-17T08:00:00+00:00",
) -> list[dict]:
    """Mock /coins/markets response (overview fields)."""
    return [
        {
            "id": "bitcoin",
            "current_price": 65000.0,
            "market_cap_rank": rank,
            "market_cap": market_cap,
            "price_change_percentage_30d_in_currency": change_30d,
            "last_updated": last_updated,
        }
    ]


# ---------------------------------------------------------------------------
# get_market_overview
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_market_overview_success() -> None:
    adapter = _adapter()
    with patch.object(
        adapter, "_get_json", new_callable=AsyncMock, return_value=_markets_response()
    ):
        ov = await adapter.get_market_overview("BTC/USDT")
    assert ov is not None
    assert ov.symbol == "BTC/USDT"
    assert ov.market_cap_rank == 1
    assert ov.market_cap == 1_300_000_000_000.0
    assert ov.price_change_pct_30d == 8.5
    assert ov.source == "coingecko"
    assert ov.timestamp_utc == "2026-06-17T08:00:00+00:00"


@pytest.mark.asyncio
async def test_get_market_overview_none_tolerant_fields() -> None:
    # Missing 30d change + market_cap must NOT void the record; rank is the core.
    adapter = _adapter()
    payload = _markets_response(market_cap=None, change_30d=None)
    with patch.object(adapter, "_get_json", new_callable=AsyncMock, return_value=payload):
        ov = await adapter.get_market_overview("BTC/USDT")
    assert ov is not None
    assert ov.market_cap_rank == 1
    assert ov.market_cap is None
    assert ov.price_change_pct_30d is None


@pytest.mark.asyncio
async def test_get_market_overview_unknown_symbol() -> None:
    adapter = _adapter()
    ov = await adapter.get_market_overview("UNKNOWN/PAIR")
    assert ov is None
    assert adapter.last_error == "unsupported_symbol"


@pytest.mark.asyncio
async def test_get_market_overview_fail_closed_on_http_error() -> None:
    adapter = _adapter()
    with patch.object(adapter, "_get_json", new_callable=AsyncMock, return_value=None):
        ov = await adapter.get_market_overview("BTC/USDT")
    assert ov is None


@pytest.mark.asyncio
async def test_get_market_overview_fail_closed_on_empty_list() -> None:
    adapter = _adapter()
    with patch.object(adapter, "_get_json", new_callable=AsyncMock, return_value=[]):
        ov = await adapter.get_market_overview("BTC/USDT")
    assert ov is None
    assert adapter.last_error == "missing_coin_payload"


# ---------------------------------------------------------------------------
# Settings — default-off contract
# ---------------------------------------------------------------------------


def test_settings_default_off() -> None:
    s = CoinGeckoOverviewSettings(_env_file=None)
    assert s.enabled is False
    assert s.api_key is None
    assert s.ttl_seconds == 900.0
    assert s.snapshot_path == Path("artifacts/coingecko_overview_cache.json")
    assert s.shadow_log_path == Path("artifacts/coingecko_overview_shadow.jsonl")


def test_settings_symbols_csv_parsing() -> None:
    s = CoinGeckoOverviewSettings(_env_file=None, symbols_csv=" btc/usdt , eth/usdt ,")
    assert s.symbols == ["BTC/USDT", "ETH/USDT"]


def test_settings_symbols_csv_empty_falls_back_to_default() -> None:
    s = CoinGeckoOverviewSettings(_env_file=None, symbols_csv="  ,  ")
    assert s.symbols == ["BTC/USDT", "ETH/USDT", "SOL/USDT"]


# ---------------------------------------------------------------------------
# Store — atomic write/read roundtrip
# ---------------------------------------------------------------------------


def _ov(symbol: str = "BTC/USDT", rank: int | None = 1) -> CoinGeckoMarketOverview:
    return CoinGeckoMarketOverview(
        symbol=symbol,
        timestamp_utc="2026-06-17T08:00:00+00:00",
        market_cap_rank=rank,
        market_cap=1.3e12,
        price_change_pct_30d=8.5,
    )


def test_store_write_read_roundtrip(tmp_path: Path) -> None:
    store = CoinGeckoOverviewStore(tmp_path / "cache.json")
    written = store.write_many([_ov("BTC/USDT", 1), _ov("ETH/USDT", 2)])
    assert written == 2
    got = store.read("BTC/USDT")
    assert got is not None
    assert got.market_cap_rank == 1
    assert store.read("ETH/USDT").market_cap_rank == 2  # type: ignore[union-attr]
    assert store.read("DOGE/USDT") is None


def test_store_missing_file_returns_empty(tmp_path: Path) -> None:
    store = CoinGeckoOverviewStore(tmp_path / "absent.json")
    assert store.read_all() == {}


def test_store_corrupt_file_returns_empty(tmp_path: Path) -> None:
    path = tmp_path / "corrupt.json"
    path.write_text("{ not json", encoding="utf-8")
    store = CoinGeckoOverviewStore(path)
    assert store.read_all() == {}


# ---------------------------------------------------------------------------
# Shadow log
# ---------------------------------------------------------------------------


def test_append_overview_shadow_log(tmp_path: Path) -> None:
    path = tmp_path / "shadow.jsonl"
    append_overview_shadow_log(path, overview=_ov("BTC/USDT", 1))
    append_overview_shadow_log(path, overview=_ov("ETH/USDT", 2))
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["symbol"] == "BTC/USDT"
    assert first["market_cap_rank"] == 1
    assert "ts" in first
    assert first["source"] == "coingecko"
