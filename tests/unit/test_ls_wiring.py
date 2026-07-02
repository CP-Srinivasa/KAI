"""build_ls_evidence_provider (Goal V5 Phase 3)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.core.domain.document import AnalysisResult
from app.core.enums import SentimentLabel
from app.core.settings import LongShortRatioEvidenceSettings
from app.market_data.models import LongShortRatioSnapshot, MarketDataPoint
from app.signals.bayesian_confidence import EvidenceKind
from app.signals.ls_snapshot_store import LongShortRatioSnapshotStore
from app.signals.ls_wiring import build_ls_evidence_provider
from app.signals.models import SignalDirection


def _analysis() -> AnalysisResult:
    return AnalysisResult(
        document_id="doc_ls_001",
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
        timestamp_utc="2026-06-11T12:00:00+00:00",
        price=65_000.0,
        volume_24h=4_000_000.0,
        change_pct_24h=2.0,
        source="mock",
    )


def _fresh_snap(ratio: float = 0.65, symbol: str = "BTC/USDT") -> LongShortRatioSnapshot:
    return LongShortRatioSnapshot(
        symbol=symbol,
        timestamp_utc=datetime.now(UTC).isoformat(),
        long_account_ratio=ratio,
        source="bybit",
    )


def _settings(
    tmp_path: Path, *, enabled: bool, trust: float = 0.5, ttl: float = 3600.0
) -> LongShortRatioEvidenceSettings:
    return LongShortRatioEvidenceSettings(
        enabled=enabled,
        source_trust=trust,
        ttl_seconds=ttl,
        snapshot_path=tmp_path / "ls.json",
        shadow_log_path=tmp_path / "ls_shadow.jsonl",
    )


# ── Default-off ───────────────────────────────────────────────────────────────


def test_disabled_returns_none(tmp_path: Path) -> None:
    assert build_ls_evidence_provider(_settings(tmp_path, enabled=False)) is None


def test_default_settings_is_disabled() -> None:
    s = LongShortRatioEvidenceSettings()
    assert s.enabled is False
    assert s.source_trust == pytest.approx(0.5)
    assert s.ttl_seconds == pytest.approx(3600.0)


# ── Enabled provider: contrarian semantics through the wiring ──────────────────


def test_crowded_long_is_contra_for_long_signal(tmp_path: Path) -> None:
    settings = _settings(tmp_path, enabled=True)
    LongShortRatioSnapshotStore(settings.snapshot_path).write_many([_fresh_snap(ratio=0.70)])
    provider = build_ls_evidence_provider(settings)
    assert provider is not None
    ev = provider(_analysis(), _md(), SignalDirection.LONG)[0]
    assert ev.kind == EvidenceKind.LONG_SHORT_RATIO
    assert ev.direction_aligned == -1  # long-crowded → contra für LONG
    assert ev.source_id == "bybit"


def test_crowded_long_is_pro_for_short_signal(tmp_path: Path) -> None:
    settings = _settings(tmp_path, enabled=True)
    LongShortRatioSnapshotStore(settings.snapshot_path).write_many([_fresh_snap(ratio=0.70)])
    provider = build_ls_evidence_provider(settings)
    assert provider is not None
    ev = provider(_analysis(), _md(), SignalDirection.SHORT)[0]
    assert ev.direction_aligned == 1  # long-crowded → pro für SHORT


def test_crowded_short_is_pro_for_long_signal(tmp_path: Path) -> None:
    settings = _settings(tmp_path, enabled=True)
    LongShortRatioSnapshotStore(settings.snapshot_path).write_many([_fresh_snap(ratio=0.30)])
    provider = build_ls_evidence_provider(settings)
    assert provider is not None
    ev = provider(_analysis(), _md(), SignalDirection.LONG)[0]
    assert ev.direction_aligned == 1  # short-crowded → pro für LONG


def test_neutral_midfield_yields_discarded_evidence(tmp_path: Path) -> None:
    # 0.50 → Deadzone → value 0 / direction 0 (still emitted, engine discards).
    settings = _settings(tmp_path, enabled=True)
    LongShortRatioSnapshotStore(settings.snapshot_path).write_many([_fresh_snap(ratio=0.50)])
    provider = build_ls_evidence_provider(settings)
    assert provider is not None
    ev = provider(_analysis(), _md(), SignalDirection.LONG)[0]
    assert ev.value == pytest.approx(0.0)
    assert ev.direction_aligned == 0


def test_writes_shadow_log(tmp_path: Path) -> None:
    settings = _settings(tmp_path, enabled=True)
    LongShortRatioSnapshotStore(settings.snapshot_path).write_many([_fresh_snap(ratio=0.62)])
    provider = build_ls_evidence_provider(settings)
    assert provider is not None
    provider(_analysis(), _md(), SignalDirection.LONG)
    lines = settings.shadow_log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["symbol"] == "BTC/USDT"
    assert rec["long_account_ratio"] == pytest.approx(0.62)
    assert rec["evidence_direction_aligned"] == -1


def test_missing_snapshot_returns_empty(tmp_path: Path) -> None:
    provider = build_ls_evidence_provider(_settings(tmp_path, enabled=True))
    assert provider is not None
    assert provider(_analysis(), _md(), SignalDirection.LONG) == ()


def test_stale_snapshot_returns_empty(tmp_path: Path) -> None:
    settings = _settings(tmp_path, enabled=True, ttl=60.0)
    old = LongShortRatioSnapshot(
        symbol="BTC/USDT",
        timestamp_utc=(datetime.now(UTC) - timedelta(hours=2)).isoformat(),
        long_account_ratio=0.70,
        source="bybit",
    )
    LongShortRatioSnapshotStore(settings.snapshot_path).write_many([old])
    provider = build_ls_evidence_provider(settings)
    assert provider is not None
    assert provider(_analysis(), _md(), SignalDirection.LONG) == ()
    assert not settings.shadow_log_path.exists()
