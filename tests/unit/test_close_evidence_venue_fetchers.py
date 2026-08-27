from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from app.execution.venues.candle_fetchers import (
    BinanceCandleFetcher,
    BybitCandleFetcher,
    CandleFetchError,
)

FetcherFactory = Callable[..., BinanceCandleFetcher | BybitCandleFetcher]


def _transport(
    payload: object, *, status: int = 200, seen: list[httpx.Request] | None = None
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, content=json.dumps(payload), request=request)

    return httpx.MockTransport(handler)


def test_binance_fetcher_maps_exclusive_window_and_parses_oldest_first() -> None:
    seen: list[httpx.Request] = []
    rows = [
        [120_000, "101", "103", "100", "102", "9", 179_999],
        [60_000, "100", "102", "99", "101", "8", 119_999],
    ]
    fetch = BinanceCandleFetcher(transport=_transport(rows, seen=seen))

    candles = fetch(
        symbol="BTC/USDT", venue="binance", interval="1m", start_ms=60_000, end_ms=180_000
    )

    assert [c.open_time_ms for c in candles] == [60_000, 120_000]
    assert candles[0].low == 99.0
    request = seen[0]
    assert request.url.path == "/api/v3/klines"
    assert dict(request.url.params) == {
        "symbol": "BTCUSDT",
        "interval": "1m",
        "startTime": "60000",
        "endTime": "179999",
        "limit": "2",
    }


def test_bybit_fetcher_checks_envelope_and_reverses_newest_first_rows() -> None:
    seen: list[httpx.Request] = []
    payload = {
        "retCode": 0,
        "retMsg": "OK",
        "result": {
            "list": [
                ["120000", "101", "103", "100", "102", "9", "900"],
                ["60000", "100", "102", "99", "101", "8", "800"],
            ]
        },
    }
    fetch = BybitCandleFetcher(transport=_transport(payload, seen=seen))

    candles = fetch(
        symbol="BTC/USDT", venue="bybit", interval="1m", start_ms=60_000, end_ms=180_000
    )

    assert [c.open_time_ms for c in candles] == [60_000, 120_000]
    assert dict(seen[0].url.params) == {
        "category": "linear",
        "symbol": "BTCUSDT",
        "interval": "1",
        "start": "60000",
        "end": "179999",
        "limit": "2",
    }


@pytest.mark.parametrize(
    ("fetcher", "venue", "payload"),
    [
        (BinanceCandleFetcher, "binance", [[60_000, "nan", "2", "1", "1", "1", 0]]),
        (
            BybitCandleFetcher,
            "bybit",
            {"retCode": 0, "retMsg": "OK", "result": {"list": [["x", "1", "2", "1", "1"]]}},
        ),
    ],
)
def test_invalid_provider_rows_fail_closed(
    fetcher: FetcherFactory, venue: str, payload: object
) -> None:
    with pytest.raises(CandleFetchError, match="invalid"):
        fetcher(transport=_transport(payload))(
            symbol="BTC/USDT", venue=venue, interval="1m", start_ms=60_000, end_ms=120_000
        )


def test_bybit_nonzero_retcode_and_http_errors_are_named() -> None:
    with pytest.raises(CandleFetchError, match="retCode=10001"):
        BybitCandleFetcher(transport=_transport({"retCode": 10001, "retMsg": "bad", "result": {}}))(
            symbol="BTC/USDT", venue="bybit", interval="1m", start_ms=0, end_ms=60_000
        )

    with pytest.raises(CandleFetchError, match="HTTP 429"):
        BinanceCandleFetcher(transport=_transport({}, status=429))(
            symbol="BTC/USDT", venue="binance", interval="1m", start_ms=0, end_ms=60_000
        )


@pytest.mark.parametrize(
    ("symbol", "venue", "message"),
    [("../../etc/passwd", "binance", "symbol"), ("BTC/USDT", "bybit", "venue")],
)
def test_fetcher_rejects_invalid_identity_before_network(
    symbol: str, venue: str, message: str
) -> None:
    with pytest.raises(CandleFetchError, match=message):
        BinanceCandleFetcher(transport=_transport([]))(
            symbol=symbol, venue=venue, interval="1m", start_ms=0, end_ms=60_000
        )
