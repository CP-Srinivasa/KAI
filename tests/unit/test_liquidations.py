"""Unit tests for the OKX liquidations adapter + TTL cache (read-only, fail-closed).

Covers aggregation (long/short split + event count), fail-closed behaviour
(non-200 / code!=0), and the non-blocking cache (cold → warming-up → populated).
"""

from __future__ import annotations

import httpx
import pytest

from app.market_data import liquidations as liq


@pytest.fixture(autouse=True)
def _clean_cache():
    liq.reset_cache_for_tests()
    yield
    liq.reset_cache_for_tests()


def _ok_payload() -> dict:
    return {
        "code": "0",
        "data": [
            {
                "details": [
                    {"posSide": "long", "sz": "5", "bkPx": "100", "ts": "1781721899148"},
                    {"posSide": "long", "sz": "2.5", "bkPx": "100", "ts": "1781721899000"},
                    {"posSide": "short", "sz": "3", "bkPx": "100", "ts": "1781721899200"},
                ]
            }
        ],
    }


def _transport(status: int, body: object) -> httpx.MockTransport:
    def handler(_req: httpx.Request) -> httpx.Response:
        if isinstance(body, (dict, list)):
            return httpx.Response(status, json=body)
        return httpx.Response(status, text=str(body))

    return httpx.MockTransport(handler)


async def test_fetch_aggregates_long_short() -> None:
    snap = await liq.fetch_liquidations(transport=_transport(200, _ok_payload()))
    assert snap.available is True and len(snap.rows) == 3  # BTC/ETH/SOL
    btc = next(r for r in snap.rows if r.symbol == "BTC/USDT")
    assert btc.long_sz == 7.5 and btc.short_sz == 3.0 and btc.events == 3
    assert btc.last_ts_utc.endswith("Z") and snap.source == "okx"


async def test_fetch_non_200_is_fail_closed() -> None:
    snap = await liq.fetch_liquidations(transport=_transport(503, "down"))
    assert snap.available is False and snap.reason


async def test_fetch_code_error_is_fail_closed() -> None:
    snap = await liq.fetch_liquidations(transport=_transport(200, {"code": "50011", "data": []}))
    assert snap.available is False


async def test_cache_cold_then_warms(monkeypatch) -> None:
    async def _fake() -> liq.LiquidationsSnapshot:
        return liq.LiquidationsSnapshot(
            available=True,
            rows=(
                liq.LiquidationRow(
                    symbol="BTC/USDT", long_sz=7.5, short_sz=3.0, events=3, last_ts_utc="x"
                ),
            ),
        )

    monkeypatch.setattr(liq, "fetch_liquidations", _fake)

    snap, age = await liq.get_cached_liquidations()
    assert snap.available is False and age is None  # cold: never blocks

    await liq._refresh_task

    snap, age = await liq.get_cached_liquidations()
    assert snap.available is True and snap.rows[0].long_sz == 7.5 and age is not None


async def test_cache_keeps_last_good_on_failure(monkeypatch) -> None:
    good = liq.LiquidationsSnapshot(
        available=True,
        rows=(
            liq.LiquidationRow(
                symbol="BTC/USDT", long_sz=1.0, short_sz=0.0, events=1, last_ts_utc="x"
            ),
        ),
    )
    seq = [good, liq.LiquidationsSnapshot.unavailable("fetch failed")]

    async def _fake() -> liq.LiquidationsSnapshot:
        return seq.pop(0)

    monkeypatch.setattr(liq, "fetch_liquidations", _fake)

    await liq.get_cached_liquidations()
    await liq._refresh_task
    assert (await liq.get_cached_liquidations())[0].available is True

    monkeypatch.setattr(liq, "_TTL_SECONDS", -1.0)
    await liq.get_cached_liquidations()
    await liq._refresh_task
    snap, _ = await liq.get_cached_liquidations()
    assert snap.available is True and snap.rows[0].long_sz == 1.0  # last good retained
