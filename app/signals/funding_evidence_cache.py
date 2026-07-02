"""Sync-Brücke vom async-Funding-Adapter zur sync-SignalGenerator-Schnittstelle.

Der ``SignalGenerator`` läuft synchron, der ``BinanceFuturesAdapter`` async.
Statt ``generate()`` über die ganze Pipeline async zu refaktorieren, hält
diese Klasse eine TTL-gecachte Funding-Rate pro Symbol vor:

  - Caller (Trading-Loop) ruft ``await cache.refresh(symbol)`` *vor* dem
    ``signal_generator.generate(...)``-Aufruf.  Der Refresh ist günstig
    (1 HTTP-Call pro Symbol) und kann im selben Cycle stattfinden, in dem
    auch Marktdaten gefetcht werden.
  - Generator-Pfad ruft ``cache.make_provider(direction_field='direction')``
    → eine sync-Funktion, die für das übergebene Symbol+Direction eine
    ``Evidence`` zurückgibt.  Bei Cache-Miss / abgelaufenem Eintrag /
    fehlender Adapter-Antwort: leere Liste — Engine läuft weiter, mit
    entsprechend höherer ``uncertainty``.

Begründung statt asyncio-Hack: ``asyncio.run()`` aus sync-Code in einem
laufenden Loop wirft, ``run_coroutine_threadsafe`` braucht Thread-Loop-
Bookkeeping.  Der TTL-Cache ist die ehrliche Antwort: Funding ändert sich
auf 8h-Skala — sub-minute-Refreshes sind ohnehin sinnlos.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable, Sequence
from typing import Protocol

from app.core.domain.document import AnalysisResult
from app.market_data.models import FundingRateSnapshot, MarketDataPoint
from app.signals.bayesian_confidence import Evidence, build_funding_rate_evidence
from app.signals.models import SignalDirection

logger = logging.getLogger(__name__)


class _FundingAdapterProto(Protocol):
    async def get_funding_rate(self, symbol: str) -> FundingRateSnapshot | None: ...


class FundingEvidenceCache:
    """In-Memory TTL-Cache + Provider-Factory für Funding-Rate-Evidence.

    Parameter:
      - adapter: liefert ``get_funding_rate(symbol)`` async.
      - ttl_seconds: Lebensdauer eines Eintrags (Default 30 min — Funding-
        Cadence ist 8 h, das ist konservativ aktuell).
      - source_trust: Vertrauen, das die Evidence in der Engine bekommt.
    """

    def __init__(
        self,
        adapter: _FundingAdapterProto,
        *,
        ttl_seconds: float = 1800.0,
        source_trust: float = 1.0,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if not 0.0 <= source_trust <= 1.0:
            raise ValueError("source_trust must be in [0, 1]")
        self._adapter = adapter
        self._ttl = ttl_seconds
        self._trust = source_trust
        self._cache: dict[str, tuple[float, FundingRateSnapshot]] = {}

    # ── Refresh (async) ───────────────────────────────────────────────────────

    async def refresh(self, symbol: str) -> FundingRateSnapshot | None:
        """Hole + cache eine einzelne Symbol-Funding-Rate.  Adapter-Fehler
        werden geloggt + verschluckt; Cache-Eintrag bleibt unverändert."""
        try:
            snap = await self._adapter.get_funding_rate(symbol)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[funding-cache] %s refresh failed: %s", symbol, exc)
            return None
        if snap is None:
            return None
        self._cache[self._key(symbol)] = (time.monotonic(), snap)
        return snap

    async def refresh_many(self, symbols: Iterable[str]) -> dict[str, FundingRateSnapshot | None]:
        out: dict[str, FundingRateSnapshot | None] = {}
        for s in symbols:
            out[s] = await self.refresh(s)
        return out

    # ── Sync-Lookup ───────────────────────────────────────────────────────────

    def get(self, symbol: str) -> FundingRateSnapshot | None:
        """Sync-Read.  Returns None bei Miss oder abgelaufenem Eintrag."""
        entry = self._cache.get(self._key(symbol))
        if entry is None:
            return None
        ts, snap = entry
        if time.monotonic() - ts > self._ttl:
            return None
        return snap

    # ── Provider-Factory für SignalGenerator ──────────────────────────────────

    def make_provider(
        self,
    ) -> Callable[[AnalysisResult, MarketDataPoint, SignalDirection], Sequence[Evidence]]:
        """Liefert eine sync-Funktion mit der vom Generator erwarteten
        Signatur ``(analysis, market_data, direction) -> Sequence[Evidence]``.
        """

        cache = self  # Closure-Bind, damit der Provider Pickle-frei bleibt

        def _provider(
            _analysis: AnalysisResult,
            market_data: MarketDataPoint,
            direction: SignalDirection,
        ) -> Sequence[Evidence]:
            snap = cache.get(market_data.symbol)
            if snap is None:
                return ()
            return [
                build_funding_rate_evidence(
                    funding_rate_pct=snap.rate * 100.0,  # Bibliothek erwartet Prozent
                    signal_is_long=(direction == SignalDirection.LONG),
                    source_trust=cache._trust,  # noqa: SLF001 — bewusster Closure-Read
                    source_id=snap.source,
                )
            ]

        return _provider

    # ── Diagnostics ───────────────────────────────────────────────────────────

    def cache_size(self) -> int:
        return len(self._cache)

    def clear(self) -> None:
        self._cache.clear()

    @staticmethod
    def _key(symbol: str) -> str:
        return symbol.strip().upper()


__all__ = ["FundingEvidenceCache"]
