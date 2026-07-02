"""get_long_short_ratio auf Bybit/Binance-Futures-Adapter (Goal V5 Phase 3).

Verifiziert: korrektes Parsen aus dem realen Response-Shape (Anteil 0..1, KEINE
Prozent-Doppel-Skalierung) + fail-safe None bei Transport-/HTTP-/Parse-/Miss-
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


# ── Bybit (newest-first list, buyRatio) ──────────────────────────────────────


@pytest.mark.asyncio
async def test_bybit_parses_buy_ratio(monkeypatch) -> None:
    import app.market_data.bybit_adapter as mod

    # Bybit returns newest-first; rows[0] is the freshest bucket.
    payload = {
        "retCode": 0,
        "result": {
            "list": [
                {"buyRatio": "0.6234", "sellRatio": "0.3766", "timestamp": "1700005000000"},
                {"buyRatio": "0.5500", "sellRatio": "0.4500", "timestamp": "1700004000000"},
            ]
        },
    }
    _patch_client(monkeypatch, mod, _FakeResponse(200, payload))
    snap = await BybitAdapter().get_long_short_ratio("BTC/USDT")
    assert snap is not None
    assert snap.symbol == "BTC/USDT"
    # buyRatio is already a fraction — no ×100 double-scaling.
    assert snap.long_account_ratio == pytest.approx(0.6234)
    assert 0.0 <= snap.long_account_ratio <= 1.0
    assert snap.source == "bybit"


@pytest.mark.asyncio
async def test_bybit_empty_list_returns_none(monkeypatch) -> None:
    import app.market_data.bybit_adapter as mod

    _patch_client(monkeypatch, mod, _FakeResponse(200, {"retCode": 0, "result": {"list": []}}))
    snap = await BybitAdapter().get_long_short_ratio("BTC/USDT")
    assert snap is None


@pytest.mark.asyncio
async def test_bybit_missing_buy_ratio_returns_none(monkeypatch) -> None:
    import app.market_data.bybit_adapter as mod

    payload = {"retCode": 0, "result": {"list": [{"timestamp": "1700005000000"}]}}
    _patch_client(monkeypatch, mod, _FakeResponse(200, payload))
    snap = await BybitAdapter().get_long_short_ratio("BTC/USDT")
    assert snap is None


@pytest.mark.asyncio
async def test_bybit_transport_error_returns_none(monkeypatch) -> None:
    import app.market_data.bybit_adapter as mod

    _patch_client(monkeypatch, mod, httpx.ConnectError("boom"))
    snap = await BybitAdapter().get_long_short_ratio("BTC/USDT")
    assert snap is None


@pytest.mark.asyncio
async def test_bybit_api_error_returns_none(monkeypatch) -> None:
    import app.market_data.bybit_adapter as mod

    _patch_client(monkeypatch, mod, _FakeResponse(200, {"retCode": 10001, "retMsg": "bad"}))
    snap = await BybitAdapter().get_long_short_ratio("BTC/USDT")
    assert snap is None


# ── Binance (oldest-first list, longAccount) ─────────────────────────────────


@pytest.mark.asyncio
async def test_binance_parses_long_account(monkeypatch) -> None:
    import app.market_data.binance_futures_adapter as mod

    # Binance hist is oldest-first; the LAST element is the freshest bucket.
    payload = [
        {"longAccount": "0.5100", "shortAccount": "0.4900", "timestamp": 1700000000000},
        {"longAccount": "0.6700", "shortAccount": "0.3300", "timestamp": 1700003600000},
    ]
    _patch_client(monkeypatch, mod, _FakeResponse(200, payload))
    snap = await BinanceFuturesAdapter().get_long_short_ratio("BTC/USDT")
    assert snap is not None
    assert snap.symbol == "BTC/USDT"
    # longAccount already a fraction; latest bucket = 0.67, no ×100.
    assert snap.long_account_ratio == pytest.approx(0.67)
    assert 0.0 <= snap.long_account_ratio <= 1.0
    assert snap.source == "binance"


@pytest.mark.asyncio
async def test_binance_empty_list_returns_none(monkeypatch) -> None:
    import app.market_data.binance_futures_adapter as mod

    _patch_client(monkeypatch, mod, _FakeResponse(200, []))
    snap = await BinanceFuturesAdapter().get_long_short_ratio("BTC/USDT")
    assert snap is None


@pytest.mark.asyncio
async def test_binance_http_error_returns_none(monkeypatch) -> None:
    import app.market_data.binance_futures_adapter as mod

    _patch_client(monkeypatch, mod, _FakeResponse(429, {}))
    snap = await BinanceFuturesAdapter().get_long_short_ratio("BTC/USDT")
    assert snap is None


@pytest.mark.asyncio
async def test_binance_garbage_json_returns_none(monkeypatch) -> None:
    import app.market_data.binance_futures_adapter as mod

    _patch_client(monkeypatch, mod, _FakeResponse(200, ValueError("not json")))
    snap = await BinanceFuturesAdapter().get_long_short_ratio("BTC/USDT")
    assert snap is None
