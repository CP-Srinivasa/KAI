"""build_funding_evidence_provider — default-off + measure-first contract."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.core.domain.document import AnalysisResult
from app.core.enums import SentimentLabel
from app.core.settings import FundingEvidenceSettings
from app.market_data.models import FundingRateSnapshot, MarketDataPoint
from app.signals.bayesian_confidence import EvidenceKind
from app.signals.funding_snapshot_store import FundingSnapshotStore
from app.signals.funding_wiring import build_funding_evidence_provider
from app.signals.models import SignalDirection


def _analysis() -> AnalysisResult:
    return AnalysisResult(
        document_id="doc_fw_001",
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


def _fresh_snap(rate: float = 0.0004, symbol: str = "BTC/USDT") -> FundingRateSnapshot:
    return FundingRateSnapshot(
        symbol=symbol,
        timestamp_utc=datetime.now(UTC).isoformat(),
        rate=rate,
        source="bybit",
    )


def _settings(
    tmp_path: Path, *, enabled: bool, trust: float = 0.5, ttl: float = 3600.0
) -> FundingEvidenceSettings:
    return FundingEvidenceSettings(
        enabled=enabled,
        source_trust=trust,
        ttl_seconds=ttl,
        snapshot_path=tmp_path / "funding.json",
        shadow_log_path=tmp_path / "shadow.jsonl",
    )


# ── Default-off invariant ─────────────────────────────────────────────────────


def test_disabled_returns_none(tmp_path: Path) -> None:
    provider = build_funding_evidence_provider(_settings(tmp_path, enabled=False))
    assert provider is None


def test_default_settings_is_disabled() -> None:
    assert FundingEvidenceSettings().enabled is False
    assert FundingEvidenceSettings().source_trust == pytest.approx(0.5)


# ── Enabled: provider builds evidence + shadow log ────────────────────────────


def test_enabled_provider_emits_funding_evidence(tmp_path: Path) -> None:
    settings = _settings(tmp_path, enabled=True)
    store = FundingSnapshotStore(settings.snapshot_path)
    store.write_many([_fresh_snap(rate=0.0004)])

    provider = build_funding_evidence_provider(settings)
    assert provider is not None
    evidences = provider(_analysis(), _md(), SignalDirection.LONG)
    assert len(evidences) == 1
    ev = evidences[0]
    assert ev.kind == EvidenceKind.FUNDING_RATE
    # positive funding + LONG → contrarian warning (aligned == -1)
    assert ev.direction_aligned == -1
    assert ev.source_trust == pytest.approx(0.5)
    assert ev.source_id == "bybit"


def test_enabled_provider_writes_shadow_log(tmp_path: Path) -> None:
    settings = _settings(tmp_path, enabled=True)
    FundingSnapshotStore(settings.snapshot_path).write_many([_fresh_snap(rate=0.0004)])
    provider = build_funding_evidence_provider(settings)
    assert provider is not None
    provider(_analysis(), _md(), SignalDirection.LONG)
    lines = settings.shadow_log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["symbol"] == "BTC/USDT"
    assert rec["rate"] == pytest.approx(0.0004)
    assert rec["source"] == "bybit"


def test_units_not_double_scaled(tmp_path: Path) -> None:
    # rate 0.0005 (Fraction) → *100 → 0.05% → exactly the 5bp saturation point.
    settings = _settings(tmp_path, enabled=True)
    FundingSnapshotStore(settings.snapshot_path).write_many([_fresh_snap(rate=0.0005)])
    provider = build_funding_evidence_provider(settings)
    assert provider is not None
    ev = provider(_analysis(), _md(), SignalDirection.LONG)[0]
    assert ev.value == pytest.approx(1.0)  # saturated, proves single *100


def test_missing_snapshot_returns_empty(tmp_path: Path) -> None:
    settings = _settings(tmp_path, enabled=True)  # no file written
    provider = build_funding_evidence_provider(settings)
    assert provider is not None
    assert provider(_analysis(), _md(), SignalDirection.LONG) == ()


def test_stale_snapshot_returns_empty(tmp_path: Path) -> None:
    settings = _settings(tmp_path, enabled=True, ttl=60.0)
    old = FundingRateSnapshot(
        symbol="BTC/USDT",
        timestamp_utc=(datetime.now(UTC) - timedelta(hours=2)).isoformat(),
        rate=0.0004,
        source="bybit",
    )
    FundingSnapshotStore(settings.snapshot_path).write_many([old])
    provider = build_funding_evidence_provider(settings)
    assert provider is not None
    # Stale beyond ttl → no evidence, no shadow log.
    assert provider(_analysis(), _md(), SignalDirection.LONG) == ()
    assert not settings.shadow_log_path.exists()
