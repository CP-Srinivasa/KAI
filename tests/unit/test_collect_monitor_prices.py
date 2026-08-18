r"""Der Preis-Einsammler des Positions-Monitors — erstmals einzeln testbar.

Der Block lag bis 2026-08-18 in ``TradingLoop.run_position_monitor`` und war nur
ueber den ganzen Loop erreichbar. Herausgezogen, weil das God-File auf seiner
Ratchet-Baseline stand: statt sie fuer zwei Zeilen anzuheben, ist
``trading_loop.py`` jetzt um 8 Zeilen KLEINER als vorher.

Zwei Eigenschaften sind hier sicherheitsrelevant:

* ``is_stale`` fuehrt zum Ueberspringen. Genau dieses Flag setzt der
  Fallback-Adapter bei Provider-Uneinigkeit UND wenn nur synthetische
  Mock-Daten vorliegen. Ein uebersprungenes Symbol heisst „lieber offen lassen
  als zu einem zweifelhaften Preis schliessen".
* Die **Herkunft** wird mitgefuehrt. Ohne sie liess sich bei den ETH-Closes vom
  11./12.08. nicht sagen, welcher Anbieter den unmoeglichen Preis lieferte.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from app.orchestrator.monitor_prices import collect_monitor_prices


@dataclass
class _Point:
    price: float
    source: str
    is_stale: bool = False


class _MarketData:
    def __init__(self, points: dict[str, object]) -> None:
        self._points = points
        self.asked: list[str] = []

    async def get_market_data_point(self, symbol: str):
        self.asked.append(symbol)
        value = self._points.get(symbol)
        if isinstance(value, Exception):
            raise value
        return value


def _collect(points: dict[str, object], symbols: list[str]):
    return asyncio.run(collect_monitor_prices(_MarketData(points), symbols))


def test_price_and_source_are_collected_together() -> None:
    got = _collect({"ETH/USDT": _Point(1874.25, "bybit")}, ["ETH/USDT"])

    assert got.by_symbol == {"ETH/USDT": 1874.25}
    assert got.sources == {"ETH/USDT": "bybit"}
    assert got.checked == 1
    assert got.no_market_data == 0


def test_stale_point_is_skipped_not_traded() -> None:
    """Der synthetische Mock-Preis kommt genau so an: vorhanden, aber stale."""
    got = _collect(
        {"ETH/USDT": _Point(3227.30, "mock|synthetic_only", is_stale=True)},
        ["ETH/USDT"],
    )

    assert got.by_symbol == {}
    assert got.sources == {}
    assert got.no_market_data == 1
    assert got.checked == 0


def test_missing_point_is_counted_not_fatal() -> None:
    got = _collect({"ETH/USDT": None}, ["ETH/USDT"])
    assert got.no_market_data == 1
    assert got.by_symbol == {}


def test_provider_error_skips_only_that_symbol() -> None:
    """Ein Fehler bei einem Symbol darf die anderen nicht mitreissen."""
    got = _collect(
        {
            "ETH/USDT": RuntimeError("transport"),
            "BTC/USDT": _Point(63_467.0, "bybit"),
        },
        ["ETH/USDT", "BTC/USDT"],
    )

    assert got.by_symbol == {"BTC/USDT": 63_467.0}
    assert got.sources == {"BTC/USDT": "bybit"}
    assert got.no_market_data == 1
    assert got.checked == 1


def test_empty_symbol_list_asks_nobody() -> None:
    md = _MarketData({})
    got = asyncio.run(collect_monitor_prices(md, []))
    assert md.asked == []
    assert got.checked == 0 and got.no_market_data == 0
