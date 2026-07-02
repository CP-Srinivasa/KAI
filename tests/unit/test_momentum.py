"""Unit tests for the Binance momentum adapter + TTL cache (read-only, fail-closed)."""

from __future__ import annotations

import httpx
import pytest

from app.market_data import momentum as mom


@pytest.fixture(autouse=True)
def _clean_cache():
    mom.reset_cache_for_tests()
    yield
    mom.reset_cache_for_tests()


def _transport(status: int, body: object) -> httpx.MockTransport:
    def handler(_req: httpx.Request) -> httpx.Response:
        if isinstance(body, (dict, list)):
            return httpx.Response(status, json=body)
        return httpx.Response(status, text=str(body))

    return httpx.MockTransport(handler)


def _ok_body() -> list[dict]:
    return [
        {"symbol": "BTCUSDT", "lastPrice": "65209.99", "priceChangePercent": "-0.716"},
        {"symbol": "ETHUSDT", "lastPrice": "1764.95", "priceChangePercent": "1.23"},
        {"symbol": "SOLUSDT", "lastPrice": "73.27", "priceChangePercent": "-2.50"},
        {"symbol": "DOGEUSDT", "lastPrice": "0.1", "priceChangePercent": "5.0"},  # ignored
    ]


async def test_fetch_maps_tracked_symbols() -> None:
    snap = await mom.fetch_momentum(transport=_transport(200, _ok_body()))
    assert snap.available is True and len(snap.rows) == 3  # DOGE ignored
    btc = next(r for r in snap.rows if r.symbol == "BTC/USDT")
    assert btc.last_price == 65209.99 and btc.change_pct_24h == -0.716
    assert snap.source == "binance"


async def test_fetch_non_200_is_fail_closed() -> None:
    snap = await mom.fetch_momentum(transport=_transport(418, "teapot"))
    assert snap.available is False and "418" in snap.reason


async def test_fetch_empty_is_fail_closed() -> None:
    snap = await mom.fetch_momentum(transport=_transport(200, []))
    assert snap.available is False


async def test_cache_cold_then_warms(monkeypatch) -> None:
    async def _fake() -> mom.MomentumSnapshot:
        return mom.MomentumSnapshot(
            available=True,
            rows=(mom.MomentumRow(symbol="BTC/USDT", last_price=65000.0, change_pct_24h=1.5),),
        )

    monkeypatch.setattr(mom, "fetch_momentum", _fake)

    snap, age = await mom.get_cached_momentum()
    assert snap.available is False and age is None  # cold: never blocks

    await mom._refresh_task

    snap, age = await mom.get_cached_momentum()
    assert snap.available is True and snap.rows[0].change_pct_24h == 1.5 and age is not None


async def test_cache_keeps_last_good_on_failure(monkeypatch) -> None:
    good = mom.MomentumSnapshot(
        available=True,
        rows=(mom.MomentumRow(symbol="BTC/USDT", last_price=65000.0, change_pct_24h=1.5),),
    )
    seq = [good, mom.MomentumSnapshot.unavailable("fetch failed")]

    async def _fake() -> mom.MomentumSnapshot:
        return seq.pop(0)

    monkeypatch.setattr(mom, "fetch_momentum", _fake)

    await mom.get_cached_momentum()
    await mom._refresh_task
    assert (await mom.get_cached_momentum())[0].available is True

    monkeypatch.setattr(mom, "_TTL_SECONDS", -1.0)
    await mom.get_cached_momentum()
    await mom._refresh_task
    snap, _ = await mom.get_cached_momentum()
    assert snap.available is True and snap.rows[0].change_pct_24h == 1.5  # last good retained
