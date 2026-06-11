"""build_oi_evidence_provider + price-alignment (Goal V5 Phase 2)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.core.domain.document import AnalysisResult
from app.core.enums import SentimentLabel
from app.core.settings import OpenInterestEvidenceSettings
from app.market_data.models import MarketDataPoint, OpenInterestSnapshot
from app.signals.bayesian_confidence import EvidenceKind
from app.signals.models import SignalDirection
from app.signals.oi_snapshot_store import OpenInterestSnapshotStore
from app.signals.oi_wiring import (
    build_oi_evidence_provider,
    price_move_aligned_with_signal,
)


def _analysis() -> AnalysisResult:
    return AnalysisResult(
        document_id="doc_oi_001",
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


def _md(symbol: str = "BTC/USDT", change_pct: float = 2.0) -> MarketDataPoint:
    return MarketDataPoint(
        symbol=symbol,
        timestamp_utc="2026-06-11T12:00:00+00:00",
        price=65_000.0,
        volume_24h=4_000_000.0,
        change_pct_24h=change_pct,
        source="mock",
    )


def _fresh_snap(z: float = 2.0, symbol: str = "BTC/USDT") -> OpenInterestSnapshot:
    return OpenInterestSnapshot(
        symbol=symbol,
        timestamp_utc=datetime.now(UTC).isoformat(),
        open_interest=12345.0,
        oi_change_zscore=z,
        source="bybit",
    )


def _settings(
    tmp_path: Path, *, enabled: bool, trust: float = 0.5, ttl: float = 3600.0
) -> OpenInterestEvidenceSettings:
    return OpenInterestEvidenceSettings(
        enabled=enabled,
        source_trust=trust,
        ttl_seconds=ttl,
        snapshot_path=tmp_path / "oi.json",
        shadow_log_path=tmp_path / "oi_shadow.jsonl",
    )


# ── price_move_aligned_with_signal: 4 Fälle ───────────────────────────────────


def test_alignment_price_up_long_is_aligned() -> None:
    assert price_move_aligned_with_signal(2.0, SignalDirection.LONG) is True


def test_alignment_price_up_short_is_not_aligned() -> None:
    assert price_move_aligned_with_signal(2.0, SignalDirection.SHORT) is False


def test_alignment_price_down_short_is_aligned() -> None:
    assert price_move_aligned_with_signal(-2.0, SignalDirection.SHORT) is True


def test_alignment_price_down_long_is_not_aligned() -> None:
    assert price_move_aligned_with_signal(-2.0, SignalDirection.LONG) is False


def test_alignment_flat_price_is_not_aligned() -> None:
    assert price_move_aligned_with_signal(0.0, SignalDirection.LONG) is False
    assert price_move_aligned_with_signal(0.0, SignalDirection.SHORT) is False


# ── Default-off ───────────────────────────────────────────────────────────────


def test_disabled_returns_none(tmp_path: Path) -> None:
    assert build_oi_evidence_provider(_settings(tmp_path, enabled=False)) is None


def test_default_settings_is_disabled() -> None:
    s = OpenInterestEvidenceSettings()
    assert s.enabled is False
    assert s.source_trust == pytest.approx(0.5)
    assert s.zscore_window == 24


# ── Enabled provider ──────────────────────────────────────────────────────────


def test_oi_up_price_aligned_confirms_signal(tmp_path: Path) -> None:
    # OI z>0 + price up + LONG → aligned move → confirmation (aligned == +1)
    settings = _settings(tmp_path, enabled=True)
    OpenInterestSnapshotStore(settings.snapshot_path).write_many([_fresh_snap(z=2.0)])
    provider = build_oi_evidence_provider(settings)
    assert provider is not None
    evidences = provider(_analysis(), _md(change_pct=2.0), SignalDirection.LONG)
    assert len(evidences) == 1
    ev = evidences[0]
    assert ev.kind == EvidenceKind.OPEN_INTEREST
    assert ev.direction_aligned == 1
    assert ev.source_id == "bybit"


def test_oi_up_price_contra_is_contra(tmp_path: Path) -> None:
    # OI z>0 + price down + LONG → contra move → contra (aligned == -1)
    settings = _settings(tmp_path, enabled=True)
    OpenInterestSnapshotStore(settings.snapshot_path).write_many([_fresh_snap(z=2.0)])
    provider = build_oi_evidence_provider(settings)
    assert provider is not None
    ev = provider(_analysis(), _md(change_pct=-2.0), SignalDirection.LONG)[0]
    assert ev.direction_aligned == -1


def test_oi_down_weakens_both_sides(tmp_path: Path) -> None:
    # OI z<0 (positions closing) → aligned == -1, magnitude halved
    settings = _settings(tmp_path, enabled=True)
    OpenInterestSnapshotStore(settings.snapshot_path).write_many([_fresh_snap(z=-3.0)])
    provider = build_oi_evidence_provider(settings)
    assert provider is not None
    ev = provider(_analysis(), _md(change_pct=2.0), SignalDirection.LONG)[0]
    assert ev.direction_aligned == -1
    assert ev.value == pytest.approx(0.5)  # |clamp(-3/3)|*0.5 = 0.5


def test_writes_shadow_log(tmp_path: Path) -> None:
    settings = _settings(tmp_path, enabled=True)
    OpenInterestSnapshotStore(settings.snapshot_path).write_many([_fresh_snap(z=1.5)])
    provider = build_oi_evidence_provider(settings)
    assert provider is not None
    provider(_analysis(), _md(change_pct=2.0), SignalDirection.LONG)
    lines = settings.shadow_log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["symbol"] == "BTC/USDT"
    assert rec["oi_change_zscore"] == pytest.approx(1.5)
    assert rec["price_move_aligned"] is True


def test_missing_snapshot_returns_empty(tmp_path: Path) -> None:
    provider = build_oi_evidence_provider(_settings(tmp_path, enabled=True))
    assert provider is not None
    assert provider(_analysis(), _md(), SignalDirection.LONG) == ()


def test_stale_snapshot_returns_empty(tmp_path: Path) -> None:
    settings = _settings(tmp_path, enabled=True, ttl=60.0)
    old = OpenInterestSnapshot(
        symbol="BTC/USDT",
        timestamp_utc=(datetime.now(UTC) - timedelta(hours=2)).isoformat(),
        open_interest=1.0,
        oi_change_zscore=2.0,
        source="bybit",
    )
    OpenInterestSnapshotStore(settings.snapshot_path).write_many([old])
    provider = build_oi_evidence_provider(settings)
    assert provider is not None
    assert provider(_analysis(), _md(), SignalDirection.LONG) == ()
    assert not settings.shadow_log_path.exists()
