"""BitMEX darf fuer abgewickelte Kontrakte keinen Kurs liefern.

Anlass (02.10.2026): Am 23.09. eroeffnete der Paper-Pfad eine MATIC/USDT-Short
zu 0.40875 — dem eingefrorenen letzten Kurs von BitMEX ``MATICUSDT``, Stand
2024-09-04, ``state: "Settled"``, ``volume24h: 0``. CoinGecko lag bei ~0.10.
Seitdem wird jeder Take-Profit als Phantom abgewiesen (164x), die Position
kommt nie zu und ``close_price_sanity`` (P0) meldet sich stuendlich.

Kein Netz: ``httpx.AsyncClient`` wird durch einen Stellvertreter ersetzt.
"""

from __future__ import annotations

import pytest

import app.market_data.bitmex_adapter as mod
from app.market_data.bitmex_adapter import BitMEXAdapter

# Antwortform von GET /api/v1/instrument?symbol=MATICUSDT, gekuerzt auf die
# angefragten Spalten (gemessen 02.10.2026 auf kai-pi5).
_SETTLED_MATIC = {
    "symbol": "MATICUSDT",
    "state": "Settled",
    "lastPrice": 0.40875,
    "timestamp": "2024-09-04T12:00:15.000Z",
    "bidPrice": None,
    "askPrice": None,
    "volume24h": 0,
}

_OPEN_XBT = {
    "symbol": "XBTUSDT",
    "state": "Open",
    "lastPrice": 86012.5,
    "timestamp": "2026-10-02T06:20:00.000Z",
    "bidPrice": 86012.0,
    "askPrice": 86013.0,
    "volume24h": 1234,
}


class _FakeResponse:
    def __init__(self, payload: object) -> None:
        self.status_code = 200
        self._payload = payload

    def json(self) -> object:
        return self._payload


class _FakeClient:
    def __init__(self, payload: object, seen: list[dict]) -> None:
        self._payload = payload
        self._seen = seen

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def get(self, url: str, params: dict | None = None) -> _FakeResponse:
        self._seen.append(dict(params or {}))
        return _FakeResponse(self._payload)


def _patch(monkeypatch: pytest.MonkeyPatch, row: dict) -> list[dict]:
    seen: list[dict] = []
    monkeypatch.setattr(mod.httpx, "AsyncClient", lambda *a, **k: _FakeClient([row], seen))
    return seen


@pytest.mark.asyncio
async def test_settled_instrument_yields_no_price(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _SETTLED_MATIC)
    adapter = BitMEXAdapter()

    assert await adapter.get_ticker("MATIC/USDT") is None
    assert adapter.last_error == "instrument_not_open:Settled"
    assert await adapter.get_price("MATIC/USDT") is None
    assert await adapter.get_market_data_point("MATIC/USDT") is None


@pytest.mark.asyncio
async def test_state_column_is_requested(monkeypatch: pytest.MonkeyPatch) -> None:
    # Ohne die Spalte liefert BitMEX den Zustand nicht mit, und die Pruefung
    # liefe ins Leere.
    seen = _patch(monkeypatch, _OPEN_XBT)
    await BitMEXAdapter().get_ticker("BTC/USDT")

    assert "state" in seen[0]["columns"].split(",")


@pytest.mark.asyncio
async def test_open_instrument_still_priced(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _OPEN_XBT)
    ticker = await BitMEXAdapter().get_ticker("BTC/USDT")

    assert ticker is not None
    assert ticker.symbol == "BTC/USDT"
    assert ticker.last == pytest.approx(86012.5)


@pytest.mark.asyncio
async def test_missing_state_keeps_previous_behaviour(monkeypatch: pytest.MonkeyPatch) -> None:
    # Fehlt das Feld (geaenderte API), bleibt der Adapter beim alten Verhalten
    # statt als Redundanzquelle ganz auszufallen.
    row = {k: v for k, v in _OPEN_XBT.items() if k != "state"}
    _patch(monkeypatch, row)

    assert await BitMEXAdapter().get_price("BTC/USDT") == pytest.approx(86012.5)
