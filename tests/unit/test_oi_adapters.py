"""get_open_interest auf Bybit/Binance-Futures-Adapter (Goal V5 Phase 2).

Verifiziert: korrektes Parsen aus dem realen Response-Shape (Serie →
vorberechneter z-score) + fail-safe None bei Transport-/HTTP-/Parse-/Miss-
Fehlern. Kein Netz — httpx via Monkeypatch gemockt.
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
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda *a, **k: _FakeClient(response))


# ── Bybit (newest-first list) ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bybit_parses_oi_and_computes_zscore(monkeypatch) -> None:
    import app.market_data.bybit_adapter as mod

    # Bybit returns newest-first. Reversed → oldest-first [10,11,12,13,14,20].
    # That is the known z=2.0 series from the z-score unit tests.
    payload = {
        "retCode": 0,
        "result": {
            "list": [
                {"openInterest": "20", "timestamp": "1700005000000"},
                {"openInterest": "14", "timestamp": "1700004000000"},
                {"openInterest": "13", "timestamp": "1700003000000"},
                {"openInterest": "12", "timestamp": "1700002000000"},
                {"openInterest": "11", "timestamp": "1700001000000"},
                {"openInterest": "10", "timestamp": "1700000000000"},
            ]
        },
    }
    _patch_client(monkeypatch, mod, _FakeResponse(200, payload))
    snap = await BybitAdapter().get_open_interest("BTC/USDT")
    assert snap is not None
    assert snap.symbol == "BTC/USDT"
    assert snap.open_interest == pytest.approx(20.0)  # latest level (newest row)
    assert snap.oi_change_zscore == pytest.approx(2.0)
    assert snap.source == "bybit"


@pytest.mark.asyncio
async def test_bybit_empty_list_returns_none(monkeypatch) -> None:
    import app.market_data.bybit_adapter as mod

    _patch_client(monkeypatch, mod, _FakeResponse(200, {"retCode": 0, "result": {"list": []}}))
    snap = await BybitAdapter().get_open_interest("BTC/USDT")
    assert snap is None


@pytest.mark.asyncio
async def test_bybit_transport_error_returns_none(monkeypatch) -> None:
    import app.market_data.bybit_adapter as mod

    _patch_client(monkeypatch, mod, httpx.ConnectError("boom"))
    snap = await BybitAdapter().get_open_interest("BTC/USDT")
    assert snap is None


@pytest.mark.asyncio
async def test_bybit_http_error_returns_none(monkeypatch) -> None:
    import app.market_data.bybit_adapter as mod

    _patch_client(monkeypatch, mod, _FakeResponse(500, {}))
    snap = await BybitAdapter().get_open_interest("BTC/USDT")
    assert snap is None


# ── Binance (oldest-first hist list) ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_binance_parses_oi_hist_and_computes_zscore(monkeypatch) -> None:
    import app.market_data.binance_futures_adapter as mod

    # Binance openInterestHist is oldest-first → use as-is. Known z=2.0 series.
    payload = [
        {"sumOpenInterest": "10", "timestamp": 1700000000000},
        {"sumOpenInterest": "11", "timestamp": 1700001000000},
        {"sumOpenInterest": "12", "timestamp": 1700002000000},
        {"sumOpenInterest": "13", "timestamp": 1700003000000},
        {"sumOpenInterest": "14", "timestamp": 1700004000000},
        {"sumOpenInterest": "20", "timestamp": 1700005000000},
    ]
    _patch_client(monkeypatch, mod, _FakeResponse(200, payload))
    snap = await BinanceFuturesAdapter().get_open_interest("BTC/USDT")
    assert snap is not None
    assert snap.symbol == "BTC/USDT"
    assert snap.open_interest == pytest.approx(20.0)  # latest = last element
    assert snap.oi_change_zscore == pytest.approx(2.0)
    assert snap.source == "binance"


@pytest.mark.asyncio
async def test_binance_symbol_not_found_returns_none(monkeypatch) -> None:
    import app.market_data.binance_futures_adapter as mod

    _patch_client(monkeypatch, mod, _FakeResponse(400, {"code": -1121}))
    snap = await BinanceFuturesAdapter().get_open_interest("NOPE/USDT")
    assert snap is None


@pytest.mark.asyncio
async def test_binance_json_decode_error_returns_none(monkeypatch) -> None:
    import app.market_data.binance_futures_adapter as mod

    _patch_client(monkeypatch, mod, _FakeResponse(200, ValueError("bad json")))
    snap = await BinanceFuturesAdapter().get_open_interest("BTC/USDT")
    assert snap is None


@pytest.mark.asyncio
async def test_binance_empty_list_returns_none(monkeypatch) -> None:
    import app.market_data.binance_futures_adapter as mod

    _patch_client(monkeypatch, mod, _FakeResponse(200, []))
    snap = await BinanceFuturesAdapter().get_open_interest("BTC/USDT")
    assert snap is None
