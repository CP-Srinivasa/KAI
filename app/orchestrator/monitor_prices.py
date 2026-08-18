"""Preise für den Positions-Monitor einsammeln — mit ihrer Herkunft.

Aus ``app/orchestrator/trading_loop.py`` herausgezogen (God-File-Ratchet,
``docs/runbooks/repo_hygiene_policy.md`` §5). Die Datei stand auf ihrer Baseline;
statt sie für zwei Zeilen anzuheben, wandert der Block hierher — damit schrumpft
das God-File, und der Schritt wird erstmals einzeln testbar.

Fachlich gehört er ohnehin nicht in die Loop-Methode: hier wird entschieden, ob
ein Preis überhaupt taugt, um damit eine Position zu schließen.

**Die Herkunft wird mitgeführt.** Bis 2026-08-18 machte der Aufrufer
``prices[symbol] = md.price`` und verwarf ``md.source``. Als am 11./12.08. zwei
ETH-Positionen zu ``3225.68635`` geschlossen wurden — dem Mock-Preis 3227,30
nach 0,05 % Slippage, während ETH bei ~1880 stand — ließ sich deshalb nicht
sagen, WELCHER Anbieter den unmöglichen Preis geliefert hatte. Die Information
war da, sie kam nur nie dort an, wo der Befund entsteht.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

logger = logging.getLogger(__name__)

__all__ = ["MonitorPrices", "collect_monitor_prices"]


class _MarketData(Protocol):
    async def get_market_data_point(self, symbol: str) -> Any: ...


class MonitorPrices:
    """Preise, ihre Herkunft und die Zahl der übersprungenen Symbole."""

    __slots__ = ("by_symbol", "no_market_data", "sources")

    def __init__(
        self,
        by_symbol: dict[str, float],
        sources: dict[str, str],
        no_market_data: int,
    ) -> None:
        self.by_symbol = by_symbol
        self.sources = sources
        self.no_market_data = no_market_data

    @property
    def checked(self) -> int:
        return len(self.by_symbol)


async def collect_monitor_prices(
    market_data: _MarketData,
    symbols: list[str],
) -> MonitorPrices:
    """Einen frischen Preis je Symbol holen, samt Anbieter.

    Übersprungen wird ein Symbol, wenn die Abfrage scheitert, nichts liefert
    oder der Punkt ``is_stale`` trägt. Genau dieses Flag setzt der
    Fallback-Adapter bei Provider-Uneinigkeit UND wenn nur synthetische
    Mock-Daten vorliegen — ein übersprungenes Symbol bedeutet also „lieber
    offen lassen als zu einem zweifelhaften Preis schließen".
    """
    by_symbol: dict[str, float] = {}
    sources: dict[str, str] = {}
    no_market_data = 0

    for symbol in symbols:
        try:
            point = await market_data.get_market_data_point(symbol)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[LOOP] monitor: market data error for %s: %s", symbol, exc)
            point = None
        if point is None or point.is_stale:
            no_market_data += 1
            continue
        by_symbol[symbol] = point.price
        sources[symbol] = point.source

    return MonitorPrices(by_symbol, sources, no_market_data)
