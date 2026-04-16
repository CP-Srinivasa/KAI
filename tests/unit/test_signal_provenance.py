"""TV-2 provenance roundtrip tests.

Invariants:
    - SignalCandidate.provenance defaults to None (legacy/RSS path stays untagged).
    - SignalProvenance is a frozen dataclass — fields are immutable.
    - Provenance is preserved when SignalCandidate is round-tripped via dataclasses.replace.
"""

from __future__ import annotations

import dataclasses

import pytest

from app.signals.models import (
    SignalCandidate,
    SignalDirection,
    SignalProvenance,
)


def _minimal_candidate(**overrides: object) -> SignalCandidate:
    base: dict[str, object] = {
        "decision_id": "dec_test",
        "timestamp_utc": "2026-04-16T12:00:00+00:00",
        "symbol": "BTC/USDT",
        "market": "crypto",
        "venue": "paper",
        "mode": "paper",
        "direction": SignalDirection.LONG,
        "thesis": "test thesis",
        "supporting_factors": ("rsi_oversold",),
        "contradictory_factors": (),
        "confidence_score": 0.8,
        "confluence_count": 2,
        "market_regime": "trending",
        "volatility_state": "normal",
        "liquidity_state": "adequate",
        "entry_price": 65000.0,
        "stop_loss_price": 64000.0,
        "take_profit_price": 67000.0,
        "invalidation_condition": "close below 64000",
        "risk_assessment": "low",
        "position_size_rationale": "1% of equity",
        "max_loss_estimate_pct": 1.0,
        "data_sources_used": ("binance",),
        "source_document_id": "doc_test",
        "model_version": "test",
        "prompt_version": "v1",
    }
    base.update(overrides)
    return SignalCandidate(**base)  # type: ignore[arg-type]


def test_signal_candidate_provenance_defaults_to_none() -> None:
    candidate = _minimal_candidate()
    assert candidate.provenance is None


def test_signal_provenance_is_frozen() -> None:
    prov = SignalProvenance(source="binance_ohlcv_rsi", version="tv-2")
    with pytest.raises(dataclasses.FrozenInstanceError):
        prov.source = "mutated"  # type: ignore[misc]


def test_signal_provenance_signal_path_id_default_none() -> None:
    prov = SignalProvenance(source="tradingview_webhook", version="tv-1")
    assert prov.signal_path_id is None


def test_signal_candidate_carries_provenance_when_set() -> None:
    prov = SignalProvenance(
        source="binance_ohlcv_rsi",
        version="tv-2",
        signal_path_id="sp_rsi_oversold_btc",
    )
    candidate = _minimal_candidate(provenance=prov)
    assert candidate.provenance is prov
    assert candidate.provenance.source == "binance_ohlcv_rsi"
    assert candidate.provenance.signal_path_id == "sp_rsi_oversold_btc"


def test_signal_candidate_replace_preserves_provenance() -> None:
    prov = SignalProvenance(source="rss", version="legacy")
    candidate = _minimal_candidate(provenance=prov)
    replaced = dataclasses.replace(candidate, confidence_score=0.9)
    assert replaced.provenance is prov
    assert replaced.confidence_score == 0.9
