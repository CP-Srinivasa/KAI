"""TTL-Cache + sync-Provider für Funding-Rate-Evidence."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.core.domain.document import AnalysisResult
from app.core.enums import SentimentLabel
from app.market_data.models import FundingRateSnapshot, MarketDataPoint
from app.signals.bayesian_confidence import EvidenceKind, build_default_engine
from app.signals.funding_evidence_cache import FundingEvidenceCache
from app.signals.models import SignalDirection


def _snap(rate: float = 0.0001, source: str = "binance_futures") -> FundingRateSnapshot:
    return FundingRateSnapshot(
        symbol="BTC/USDT",
        timestamp_utc="2026-05-09T12:00:00+00:00",
        rate=rate,
        source=source,
    )


def _analysis() -> AnalysisResult:
    return AnalysisResult(
        document_id="doc_fc_001",
        sentiment_label=SentimentLabel.BULLISH,
        sentiment_score=0.8,
        relevance_score=0.8,
        impact_score=0.7,
        confidence_score=0.8,
        novelty_score=0.6,
        actionable=True,
        affected_assets=["BTC"],
        tags=["t"],
        spam_probability=0.05,
        explanation_short="thesis>=10ch",
        explanation_long="long",
    )


def _md(symbol: str = "BTC/USDT") -> MarketDataPoint:
    return MarketDataPoint(
        symbol=symbol,
        timestamp_utc="2026-05-09T12:00:00+00:00",
        price=65_000.0,
        volume_24h=4_000_000.0,
        change_pct_24h=2.0,
        source="mock",
    )


class _StubAdapter:
    def __init__(self, snap: FundingRateSnapshot | None = None, raise_exc: Exception | None = None):
        self._snap = snap
        self._exc = raise_exc
        self.calls = 0

    async def get_funding_rate(self, symbol: str) -> FundingRateSnapshot | None:
        self.calls += 1
        if self._exc is not None:
            raise self._exc
        return self._snap


# ── Constructor-Validierung ──────────────────────────────────────────────────


def test_invalid_ttl_rejected() -> None:
    with pytest.raises(ValueError):
        FundingEvidenceCache(_StubAdapter(), ttl_seconds=0)


def test_invalid_trust_rejected() -> None:
    with pytest.raises(ValueError):
        FundingEvidenceCache(_StubAdapter(), source_trust=1.5)


# ── Refresh + Get ────────────────────────────────────────────────────────────


def test_refresh_populates_cache_then_get_returns_snapshot() -> None:
    adapter = _StubAdapter(snap=_snap(rate=0.0002))
    cache = FundingEvidenceCache(adapter)
    asyncio.run(cache.refresh("BTC/USDT"))
    snap = cache.get("BTC/USDT")
    assert snap is not None
    assert snap.rate == pytest.approx(0.0002)
    assert cache.cache_size() == 1


def test_get_returns_none_when_missing() -> None:
    cache = FundingEvidenceCache(_StubAdapter(snap=_snap()))
    assert cache.get("UNKNOWN/USDT") is None


def test_refresh_failure_is_swallowed() -> None:
    adapter = _StubAdapter(raise_exc=RuntimeError("boom"))
    cache = FundingEvidenceCache(adapter)
    snap = asyncio.run(cache.refresh("BTC/USDT"))
    assert snap is None
    assert cache.cache_size() == 0


def test_refresh_none_response_does_not_overwrite_cache() -> None:
    cache = FundingEvidenceCache(_StubAdapter(snap=_snap(rate=0.0001)))
    asyncio.run(cache.refresh("BTC/USDT"))
    cache._adapter._snap = None  # noqa: SLF001
    asyncio.run(cache.refresh("BTC/USDT"))
    snap = cache.get("BTC/USDT")
    assert snap is not None
    assert snap.rate == pytest.approx(0.0001)


def test_refresh_many_iterates_symbols() -> None:
    adapter = AsyncMock()
    adapter.get_funding_rate.side_effect = [_snap(rate=0.0001), _snap(rate=0.0003)]
    cache = FundingEvidenceCache(adapter)
    out = asyncio.run(cache.refresh_many(["BTC/USDT", "ETH/USDT"]))
    assert set(out.keys()) == {"BTC/USDT", "ETH/USDT"}
    assert cache.cache_size() == 2


def test_ttl_expiry_invalidates_cache() -> None:
    cache = FundingEvidenceCache(_StubAdapter(snap=_snap()), ttl_seconds=0.01)
    asyncio.run(cache.refresh("BTC/USDT"))
    assert cache.get("BTC/USDT") is not None
    import time as _time

    _time.sleep(0.05)
    assert cache.get("BTC/USDT") is None


def test_clear_resets_cache() -> None:
    cache = FundingEvidenceCache(_StubAdapter(snap=_snap()))
    asyncio.run(cache.refresh("BTC/USDT"))
    cache.clear()
    assert cache.cache_size() == 0


def test_symbol_keys_are_case_insensitive() -> None:
    cache = FundingEvidenceCache(_StubAdapter(snap=_snap()))
    asyncio.run(cache.refresh("btc/usdt"))
    assert cache.get("BTC/USDT") is not None


# ── Provider-Factory ─────────────────────────────────────────────────────────


def test_provider_returns_evidence_when_cached() -> None:
    cache = FundingEvidenceCache(_StubAdapter(snap=_snap(rate=0.0004)))
    asyncio.run(cache.refresh("BTC/USDT"))
    provider = cache.make_provider()
    evidences = provider(_analysis(), _md(), SignalDirection.LONG)
    assert len(evidences) == 1
    assert evidences[0].kind == EvidenceKind.FUNDING_RATE


def test_provider_returns_empty_when_no_cache() -> None:
    cache = FundingEvidenceCache(_StubAdapter(snap=_snap()))
    provider = cache.make_provider()
    evidences = provider(_analysis(), _md(), SignalDirection.LONG)
    assert evidences == ()


def test_provider_evidence_inverts_for_long_vs_short() -> None:
    """Positive Funding-Rate → contra-LONG, pro-SHORT (Engine-Konvention)."""
    cache = FundingEvidenceCache(_StubAdapter(snap=_snap(rate=0.0005)))
    asyncio.run(cache.refresh("BTC/USDT"))
    provider = cache.make_provider()

    ev_long = provider(_analysis(), _md(), SignalDirection.LONG)[0]
    ev_short = provider(_analysis(), _md(), SignalDirection.SHORT)[0]
    # Beide haben dieselbe Magnitude, aber gegensätzliche Direction
    assert ev_long.direction_aligned == -1
    assert ev_short.direction_aligned == +1


def test_provider_evidence_lowers_posterior_for_crowded_long() -> None:
    cache = FundingEvidenceCache(_StubAdapter(snap=_snap(rate=0.0008)))
    asyncio.run(cache.refresh("BTC/USDT"))
    provider = cache.make_provider()
    evidences = provider(_analysis(), _md(), SignalDirection.LONG)

    engine = build_default_engine()
    base = engine.evaluate([], prior_probability=0.6)
    boosted = engine.evaluate(evidences, prior_probability=0.6)
    assert boosted.posterior_probability < base.posterior_probability
