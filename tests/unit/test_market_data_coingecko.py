"""Unit tests for the read-only CoinGecko market data adapter."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from app.market_data.coingecko_adapter import CoinGeckoAdapter
from app.market_data.service import get_market_data_snapshot


@pytest.mark.asyncio
async def test_coingecko_snapshot_success(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = CoinGeckoAdapter(freshness_threshold_seconds=120.0)
    now_epoch = int(datetime.now(UTC).timestamp())

    async def fake_get_json(
        _url: str, *, params: dict[str, str] | None = None
    ) -> list[dict[str, object]]:
        assert params is not None
        assert params["ids"] == "bitcoin"
        return [
            {
                "id": "bitcoin",
                "current_price": 65000.0,
                "total_volume": 123456.0,
                "price_change_percentage_24h": 2.5,
                "price_change_percentage_7d_in_currency": 4.0,
                "last_updated": datetime.fromtimestamp(now_epoch, tz=UTC).isoformat(),
            }
        ]

    monkeypatch.setattr(adapter, "_get_json", fake_get_json)

    snapshot = await adapter.get_market_data_snapshot("BTC/USDT")

    assert snapshot.available is True
    assert snapshot.symbol == "BTC/USDT"
    assert snapshot.provider == "coingecko"
    assert snapshot.price == 65000.0
    assert snapshot.is_stale is False
    assert snapshot.error is None
    assert snapshot.execution_enabled is False
    assert snapshot.write_back_allowed is False


@pytest.mark.asyncio
async def test_coingecko_snapshot_marks_stale_data(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = CoinGeckoAdapter(freshness_threshold_seconds=5.0)
    old_epoch = int(datetime.now(UTC).timestamp()) - 600

    async def fake_get_json(
        _url: str, *, params: dict[str, str] | None = None
    ) -> list[dict[str, object]]:
        assert params is not None
        return [
            {
                "id": "bitcoin",
                "current_price": 64000.0,
                "total_volume": 0.0,
                "price_change_percentage_24h": 0.0,
                "price_change_percentage_7d_in_currency": 0.0,
                "last_updated": datetime.fromtimestamp(old_epoch, tz=UTC).isoformat(),
            }
        ]

    monkeypatch.setattr(adapter, "_get_json", fake_get_json)

    snapshot = await adapter.get_market_data_snapshot("BTC/USDT")

    assert snapshot.available is True
    assert snapshot.is_stale is True
    assert snapshot.error == "stale_data"
    assert snapshot.freshness_seconds is not None
    assert snapshot.freshness_seconds > 5.0


@pytest.mark.asyncio
async def test_coingecko_snapshot_fail_closed_on_missing_price(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = CoinGeckoAdapter()

    async def fake_get_json(
        _url: str, *, params: dict[str, str] | None = None
    ) -> list[dict[str, object]]:
        assert params is not None
        return [{"id": "bitcoin", "total_volume": 1000.0}]

    monkeypatch.setattr(adapter, "_get_json", fake_get_json)

    snapshot = await adapter.get_market_data_snapshot("BTC/USDT")

    assert snapshot.available is False
    assert snapshot.price is None
    assert snapshot.error == "missing_or_invalid_price"
    assert snapshot.is_stale is True


@pytest.mark.asyncio
async def test_coingecko_get_json_timeout_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = CoinGeckoAdapter(timeout_seconds=1)

    class _TimeoutClient:
        def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
            pass

        async def __aenter__(self) -> _TimeoutClient:
            return self

        async def __aexit__(self, exc_type, exc, tb) -> bool:  # noqa: ANN001
            return False

        async def get(self, _url: str, params=None):  # noqa: ANN001
            raise httpx.TimeoutException("timeout")

    monkeypatch.setattr("app.market_data.coingecko_adapter.httpx.AsyncClient", _TimeoutClient)

    payload = await adapter._get_json("https://api.coingecko.com/api/v3/simple/price")

    assert payload is None
    assert adapter.last_error == "timeout"


@pytest.mark.asyncio
async def test_market_data_service_fail_closed_on_unsupported_provider() -> None:
    snapshot = await get_market_data_snapshot(
        symbol="BTC/USDT",
        provider="unsupported",
    )

    assert snapshot.available is False
    assert snapshot.error == "unsupported_provider:unsupported"
    assert snapshot.execution_enabled is False
    assert snapshot.write_back_allowed is False
