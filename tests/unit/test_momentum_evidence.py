"""Tests for the G3 Momentum-Universe Bayesian evidence (shadow, inert).

Locks: the evidence is inert (direction_aligned=0 → zero contribution) until a
sign is learned + promoted; value = momentum extremity from the neutral median;
the provider is default-off and fail-safe (no/stale snapshot, symbol absent →
empty), and writes a measure-first shadow record when it does fire.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.core.evidence_settings import MomentumUniverseEvidenceSettings
from app.market_data.models import MarketDataPoint
from app.observability.momentum_universe import RankedSymbol
from app.observability.momentum_universe_ledger import append_snapshot
from app.signals.bayesian_confidence import (
    EvidenceKind,
    _calibrate,
    build_momentum_evidence,
)
from app.signals.models import SignalDirection
from app.signals.momentum_evidence_features import read_universe_scores
from app.signals.momentum_wiring import build_momentum_evidence_provider


def _mdp(symbol: str) -> MarketDataPoint:
    return MarketDataPoint(
        symbol=symbol,
        timestamp_utc="2026-06-26T00:00:00Z",
        price=100.0,
        volume_24h=1.0,
        change_pct_24h=0.0,
        source="test",
    )


def _settings(tmp_path: Path, **over: object) -> MomentumUniverseEvidenceSettings:
    defaults: dict[str, object] = {
        "enabled": True,
        "ledger_path": tmp_path / "u.jsonl",
        "shadow_log_path": tmp_path / "shadow.jsonl",
        "source_trust": 0.5,
        "direction_aligned": 0,
        "ttl_seconds": 90000.0,
    }
    defaults.update(over)
    return MomentumUniverseEvidenceSettings(**defaults)  # type: ignore[arg-type]


class TestBuilder:
    def test_inert_zero_contribution(self) -> None:
        ev = build_momentum_evidence(momentum_score=0.95, source_trust=0.5)
        assert ev.kind == EvidenceKind.MOMENTUM
        assert ev.direction_aligned == 0
        assert _calibrate(EvidenceKind.MOMENTUM, ev.value, ev.direction_aligned) == 0.0

    def test_value_is_extremity_from_neutral(self) -> None:
        assert build_momentum_evidence(momentum_score=0.5).value == 0.0
        assert build_momentum_evidence(momentum_score=1.0).value == pytest.approx(1.0)
        assert build_momentum_evidence(momentum_score=0.0).value == pytest.approx(1.0)
        assert build_momentum_evidence(momentum_score=None).value == 0.0

    def test_learned_direction_contributes(self) -> None:
        ev = build_momentum_evidence(momentum_score=0.95, direction_aligned=1)
        assert _calibrate(EvidenceKind.MOMENTUM, ev.value, ev.direction_aligned) > 0.0


class TestProvider:
    def test_disabled_returns_none(self, tmp_path: Path) -> None:
        assert build_momentum_evidence_provider(_settings(tmp_path, enabled=False)) is None

    def test_enabled_writes_shadow_and_returns_inert(self, tmp_path: Path) -> None:
        ledger = tmp_path / "u.jsonl"
        append_snapshot(
            ledger, [RankedSymbol("BTC/USDT", 0.9, 0.8, 0.95, 1, {})], now=datetime.now(UTC)
        )
        provider = build_momentum_evidence_provider(_settings(tmp_path))
        assert provider is not None
        out = provider(None, _mdp("BTC/USDT"), SignalDirection.LONG)  # type: ignore[arg-type]
        assert len(out) == 1
        assert out[0].direction_aligned == 0
        shadow = (tmp_path / "shadow.jsonl").read_text(encoding="utf-8")
        assert "BTC/USDT" in shadow
        assert "momentum_score" in shadow

    def test_symbol_not_in_universe_yields_empty(self, tmp_path: Path) -> None:
        ledger = tmp_path / "u.jsonl"
        append_snapshot(
            ledger, [RankedSymbol("BTC/USDT", 0.9, 0.8, 0.95, 1, {})], now=datetime.now(UTC)
        )
        provider = build_momentum_evidence_provider(_settings(tmp_path))
        assert provider is not None
        assert provider(None, _mdp("XRP/USDT"), SignalDirection.LONG) == ()  # type: ignore[arg-type]

    def test_stale_snapshot_yields_empty(self, tmp_path: Path) -> None:
        ledger = tmp_path / "u.jsonl"
        append_snapshot(
            ledger,
            [RankedSymbol("BTC/USDT", 0.9, 0.8, 0.95, 1, {})],
            now=datetime(2020, 1, 1, tzinfo=UTC),
        )
        provider = build_momentum_evidence_provider(_settings(tmp_path, ttl_seconds=3600.0))
        assert provider is not None
        assert provider(None, _mdp("BTC/USDT"), SignalDirection.LONG) == ()  # type: ignore[arg-type]

    def test_no_snapshot_yields_empty(self, tmp_path: Path) -> None:
        provider = build_momentum_evidence_provider(_settings(tmp_path))
        assert provider is not None
        assert provider(None, _mdp("BTC/USDT"), SignalDirection.LONG) == ()  # type: ignore[arg-type]


class TestFeatures:
    def test_read_universe_scores(self, tmp_path: Path) -> None:
        ledger = tmp_path / "u.jsonl"
        append_snapshot(
            ledger,
            [RankedSymbol("BTC/USDT", 0.91, 0.88, 0.95, 1, {})],
            now=datetime(2026, 6, 26, tzinfo=UTC),
        )
        ts, scores = read_universe_scores(ledger)
        assert ts is not None and ts.startswith("2026-06-26")
        assert scores["BTC/USDT"]["momentum_score"] == 0.95
        assert scores["BTC/USDT"]["volume_score"] == 0.88

    def test_read_missing_returns_empty(self, tmp_path: Path) -> None:
        ts, scores = read_universe_scores(tmp_path / "nope.jsonl")
        assert ts is None
        assert scores == {}


class TestEval:
    def test_momentum_score_feeds_direction_eval(self) -> None:
        # The G3 eval reuses the generic L2 bootstrap with feature_key="momentum_score":
        # high-momentum (score > 0.5) measurements joined to positive forward returns,
        # low-momentum to negative → mean_high must beat mean_low (direction learnable).
        from app.observability.l2_evidence_eval import evaluate_feature_direction, pit_join

        measurements = [
            {"ts": "2026-06-01T00:00:00Z", "symbol": "BTC/USDT", "momentum_score": 0.9},
            {"ts": "2026-06-01T00:00:00Z", "symbol": "ETH/USDT", "momentum_score": 0.1},
        ]
        outcomes = [
            {"symbol": "BTC/USDT", "entry_ts": "2026-06-01T01:00:00Z", "net_bps": 50.0},
            {"symbol": "ETH/USDT", "entry_ts": "2026-06-01T01:00:00Z", "net_bps": -50.0},
        ]
        pairs = pit_join(measurements, outcomes)
        result = evaluate_feature_direction(pairs, feature_key="momentum_score", min_sample=1)
        assert result["mean_high"] > result["mean_low"]
