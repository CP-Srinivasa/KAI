"""build_composite_evidence_provider — Phase 1+2 combination contract.

Beweist die harte Invariante: Funding-Verhalten regresst NICHT, Default OFF
ergibt keinen Provider, und beide-an verkettet die Evidenzen.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.core.domain.document import AnalysisResult
from app.core.enums import SentimentLabel
from app.core.settings import FundingEvidenceSettings, OpenInterestEvidenceSettings
from app.market_data.models import FundingRateSnapshot, MarketDataPoint, OpenInterestSnapshot
from app.signals.bayesian_confidence import EvidenceKind
from app.signals.composite_evidence_wiring import build_composite_evidence_provider
from app.signals.funding_snapshot_store import FundingSnapshotStore
from app.signals.funding_wiring import build_funding_evidence_provider
from app.signals.models import SignalDirection
from app.signals.oi_snapshot_store import OpenInterestSnapshotStore


def _analysis() -> AnalysisResult:
    return AnalysisResult(
        document_id="doc_c_001",
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


def _md() -> MarketDataPoint:
    return MarketDataPoint(
        symbol="BTC/USDT",
        timestamp_utc="2026-06-11T12:00:00+00:00",
        price=65_000.0,
        volume_24h=4_000_000.0,
        change_pct_24h=2.0,
        source="mock",
    )


def _funding_settings(tmp_path: Path, *, enabled: bool) -> FundingEvidenceSettings:
    return FundingEvidenceSettings(
        enabled=enabled,
        source_trust=0.5,
        snapshot_path=tmp_path / "funding.json",
        shadow_log_path=tmp_path / "funding_shadow.jsonl",
    )


def _oi_settings(tmp_path: Path, *, enabled: bool) -> OpenInterestEvidenceSettings:
    return OpenInterestEvidenceSettings(
        enabled=enabled,
        source_trust=0.5,
        snapshot_path=tmp_path / "oi.json",
        shadow_log_path=tmp_path / "oi_shadow.jsonl",
    )


def _seed_funding(tmp_path: Path) -> None:
    FundingSnapshotStore(tmp_path / "funding.json").write_many(
        [
            FundingRateSnapshot(
                symbol="BTC/USDT",
                timestamp_utc=datetime.now(UTC).isoformat(),
                rate=0.0004,
                source="bybit",
            )
        ]
    )


def _seed_oi(tmp_path: Path) -> None:
    OpenInterestSnapshotStore(tmp_path / "oi.json").write_many(
        [
            OpenInterestSnapshot(
                symbol="BTC/USDT",
                timestamp_utc=datetime.now(UTC).isoformat(),
                open_interest=12345.0,
                oi_change_zscore=2.0,
                source="bybit",
            )
        ]
    )


# ── both off → no provider ────────────────────────────────────────────────────


def test_both_off_returns_none(tmp_path: Path) -> None:
    provider = build_composite_evidence_provider(
        _funding_settings(tmp_path, enabled=False),
        _oi_settings(tmp_path, enabled=False),
    )
    assert provider is None


# ── only funding on → identical to the standalone funding provider ────────────


def test_only_funding_on_does_not_regress(tmp_path: Path) -> None:
    _seed_funding(tmp_path)
    fs = _funding_settings(tmp_path, enabled=True)
    composite = build_composite_evidence_provider(fs, _oi_settings(tmp_path, enabled=False))
    standalone = build_funding_evidence_provider(fs)
    assert composite is not None and standalone is not None

    comp_ev = composite(_analysis(), _md(), SignalDirection.LONG)
    std_ev = standalone(_analysis(), _md(), SignalDirection.LONG)
    assert len(comp_ev) == len(std_ev) == 1
    assert comp_ev[0].kind == std_ev[0].kind == EvidenceKind.FUNDING_RATE
    assert comp_ev[0].value == pytest.approx(std_ev[0].value)
    assert comp_ev[0].direction_aligned == std_ev[0].direction_aligned
    # No OI evidence leaked into the funding-only path.
    assert all(e.kind != EvidenceKind.OPEN_INTEREST for e in comp_ev)


# ── only OI on ────────────────────────────────────────────────────────────────


def test_only_oi_on(tmp_path: Path) -> None:
    _seed_oi(tmp_path)
    composite = build_composite_evidence_provider(
        _funding_settings(tmp_path, enabled=False),
        _oi_settings(tmp_path, enabled=True),
    )
    assert composite is not None
    ev = composite(_analysis(), _md(), SignalDirection.LONG)
    assert len(ev) == 1
    assert ev[0].kind == EvidenceKind.OPEN_INTEREST


# ── both on → both evidences, funding first ───────────────────────────────────


def test_both_on_chains_both_evidences(tmp_path: Path) -> None:
    _seed_funding(tmp_path)
    _seed_oi(tmp_path)
    composite = build_composite_evidence_provider(
        _funding_settings(tmp_path, enabled=True),
        _oi_settings(tmp_path, enabled=True),
    )
    assert composite is not None
    ev = composite(_analysis(), _md(), SignalDirection.LONG)
    kinds = [e.kind for e in ev]
    assert kinds == [EvidenceKind.FUNDING_RATE, EvidenceKind.OPEN_INTEREST]


def test_both_on_one_missing_snapshot_still_yields_other(tmp_path: Path) -> None:
    # funding seeded, OI snapshot absent → composite still returns funding ev,
    # no exception from the empty OI sub-provider.
    _seed_funding(tmp_path)
    composite = build_composite_evidence_provider(
        _funding_settings(tmp_path, enabled=True),
        _oi_settings(tmp_path, enabled=True),
    )
    assert composite is not None
    ev = composite(_analysis(), _md(), SignalDirection.LONG)
    assert [e.kind for e in ev] == [EvidenceKind.FUNDING_RATE]
