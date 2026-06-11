"""get_funding_rate auf Bybit/Binance-Futures-Adapter (Goal V5 Phase 1).

Verifiziert: korrektes Parsen aus dem realen Response-Shape + fail-safe
None bei Transport-/HTTP-/Parse-/Miss-Fehlern. Kein Netz — httpx wird via
respx/Monkeypatch gemockt.
"""

from __future__ import annotations

import httpx
import pytest

from app.market_data.binance_futures_adapter import BinanceFuturesAdapter
from app.market_data.bybit_adapter import BybitAdapter


class _FakeResponse:
    def __init__(self, status_code: int, payload: object) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = "fake"

    def json(self) -> object:
        if isinstance(self._payload, ValueError):
            raise self._payload
        return self._payload


class _FakeClient:
    """Minimal async-context httpx.AsyncClient stand-in."""

    def __init__(self, response: _FakeResponse | Exception) -> None:
        self._response = response

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def get(self, url: str, params: dict | None = None) -> _FakeResponse:
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def _patch_client(monkeypatch: pytest.MonkeyPatch, module, response) -> None:
    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda *a, **k: _FakeClient(response),
    )


# ── Bybit ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bybit_parses_funding_from_ticker_response(monkeypatch) -> None:
    import app.market_data.bybit_adapter as mod

    payload = {
        "retCode": 0,
        "time": 1_700_000_000_000,
        "result": {
            "list": [
                {
                    "symbol": "BTCUSDT",
                    "fundingRate": "0.0001",
                    "nextFundingTime": "1700001000000",
                    "markPrice": "65000.5",
                    "indexPrice": "64999.0",
                }
            ]
        },
    }
    _patch_client(monkeypatch, mod, _FakeResponse(200, payload))
    snap = await BybitAdapter().get_funding_rate("BTC/USDT")
    assert snap is not None
    assert snap.symbol == "BTC/USDT"
    assert snap.rate == pytest.approx(0.0001)  # Fraction, NOT *100
    assert snap.mark_price == pytest.approx(65000.5)
    assert snap.index_price == pytest.approx(64999.0)
    assert snap.next_funding_time_utc is not None
    assert snap.source == "bybit"


@pytest.mark.asyncio
async def test_bybit_no_funding_field_returns_none(monkeypatch) -> None:
    import app.market_data.bybit_adapter as mod

    payload = {
        "retCode": 0,
        "time": 1_700_000_000_000,
        "result": {"list": [{"symbol": "BTCUSDT"}]},  # no fundingRate
    }
    _patch_client(monkeypatch, mod, _FakeResponse(200, payload))
    adapter = BybitAdapter()
    assert await adapter.get_funding_rate("BTC/USDT") is None
    assert adapter.last_error == "no_funding_rate"


@pytest.mark.asyncio
async def test_bybit_transport_error_returns_none(monkeypatch) -> None:
    import app.market_data.bybit_adapter as mod

    _patch_client(monkeypatch, mod, httpx.ConnectError("boom"))
    assert await BybitAdapter().get_funding_rate("BTC/USDT") is None


@pytest.mark.asyncio
async def test_bybit_bad_rate_returns_none(monkeypatch) -> None:
    import app.market_data.bybit_adapter as mod

    payload = {
        "retCode": 0,
        "result": {"list": [{"symbol": "BTCUSDT", "fundingRate": "not-a-number"}]},
    }
    _patch_client(monkeypatch, mod, _FakeResponse(200, payload))
    adapter = BybitAdapter()
    assert await adapter.get_funding_rate("BTC/USDT") is None
    assert adapter.last_error == "funding_parse_error"


@pytest.mark.asyncio
async def test_bybit_empty_symbol_returns_none() -> None:
    adapter = BybitAdapter()
    assert await adapter.get_funding_rate("") is None
    assert adapter.last_error == "empty_symbol"


# ── Binance Futures ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_binance_parses_premium_index(monkeypatch) -> None:
    import app.market_data.binance_futures_adapter as mod

    payload = {
        "symbol": "ETHUSDT",
        "lastFundingRate": "-0.00025",
        "nextFundingTime": 1_700_001_000_000,
        "markPrice": "3200.10",
        "indexPrice": "3199.50",
        "time": 1_700_000_000_000,
    }
    _patch_client(monkeypatch, mod, _FakeResponse(200, payload))
    snap = await BinanceFuturesAdapter().get_funding_rate("ETH/USDT")
    assert snap is not None
    assert snap.symbol == "ETH/USDT"
    assert snap.rate == pytest.approx(-0.00025)  # Fraction, sign preserved
    assert snap.mark_price == pytest.approx(3200.10)
    assert snap.next_funding_time_utc is not None
    assert snap.source == "binance"


@pytest.mark.asyncio
async def test_binance_symbol_not_found_returns_none(monkeypatch) -> None:
    import app.market_data.binance_futures_adapter as mod

    _patch_client(monkeypatch, mod, _FakeResponse(400, {"code": -1121}))
    adapter = BinanceFuturesAdapter()
    assert await adapter.get_funding_rate("NOPE/USDT") is None
    assert adapter.last_error == "symbol_not_found"


@pytest.mark.asyncio
async def test_binance_rate_limited_returns_none(monkeypatch) -> None:
    import app.market_data.binance_futures_adapter as mod

    _patch_client(monkeypatch, mod, _FakeResponse(429, {}))
    adapter = BinanceFuturesAdapter()
    assert await adapter.get_funding_rate("BTC/USDT") is None
    assert adapter.last_error == "rate_limited"


@pytest.mark.asyncio
async def test_binance_json_decode_error_returns_none(monkeypatch) -> None:
    import app.market_data.binance_futures_adapter as mod

    _patch_client(monkeypatch, mod, _FakeResponse(200, ValueError("bad json")))
    adapter = BinanceFuturesAdapter()
    assert await adapter.get_funding_rate("BTC/USDT") is None
    assert adapter.last_error == "json_decode_error"


@pytest.mark.asyncio
async def test_binance_missing_field_returns_none(monkeypatch) -> None:
    import app.market_data.binance_futures_adapter as mod

    _patch_client(monkeypatch, mod, _FakeResponse(200, {"symbol": "BTCUSDT"}))
    adapter = BinanceFuturesAdapter()
    assert await adapter.get_funding_rate("BTC/USDT") is None
    assert adapter.last_error == "unexpected_payload"
