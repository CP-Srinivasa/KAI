"""Hype-Evidence-Wiring (HYPE-S1) — Default-off-, Fail-safe- und Emissions-Vertrag.

Risiken, die diese Tests abdecken: (1) Verhaltenswechsel ohne Operator-Opt-in,
(2) Dämpfung auf totem/fehlendem Snapshot, (3) Verletzung des
dampen_only-Sicherheitsvertrags (Hype darf keine Shorts begründen),
(4) Shadow-Log-Lücken (measure-first braucht auch die NICHT emittierten Fälle).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.core.domain.document import AnalysisResult
from app.core.enums import SentimentLabel
from app.core.evidence_settings import HypeEvidenceSettings
from app.market_data.models import MarketDataPoint
from app.signals.bayesian_confidence import EvidenceKind
from app.signals.hype_snapshot_store import HypeSnapshot, HypeSnapshotStore
from app.signals.hype_wiring import build_hype_evidence_provider
from app.signals.models import SignalDirection


def _analysis() -> AnalysisResult:
    return AnalysisResult(
        document_id="doc_h_001",
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
        timestamp_utc="2026-06-12T12:00:00+00:00",
        price=65_000.0,
        volume_24h=4_000_000.0,
        change_pct_24h=2.0,
        source="mock",
    )


def _settings(tmp_path: Path, **overrides: object) -> HypeEvidenceSettings:
    base: dict[str, object] = {
        "enabled": True,
        "source_trust": 0.5,
        "snapshot_path": tmp_path / "hype.json",
        "shadow_log_path": tmp_path / "hype_shadow.jsonl",
        "min_score_for_evidence": 0.3,
    }
    base.update(overrides)
    return HypeEvidenceSettings(**base)  # type: ignore[arg-type]


def _seed(
    tmp_path: Path,
    *,
    score: float = 0.8,
    age_seconds: float = 0.0,
    insufficient: bool = False,
) -> None:
    ts = datetime.now(UTC) - timedelta(seconds=age_seconds)
    HypeSnapshotStore(tmp_path / "hype.json").write_many(
        [
            HypeSnapshot(
                asset="BTC",
                timestamp_utc=ts.isoformat(),
                hype_score=score,
                velocity_ratio=4.0,
                mentions_recent=20,
                distinct_sources_recent=6,
                one_sidedness=0.9,
                insufficient_data=insufficient,
            )
        ]
    )


def _shadow_lines(tmp_path: Path) -> list[dict[str, object]]:
    p = tmp_path / "hype_shadow.jsonl"
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line]


# ── Default-off-Vertrag ───────────────────────────────────────────────────────


def test_disabled_returns_no_provider() -> None:
    assert build_hype_evidence_provider(HypeEvidenceSettings(enabled=False)) is None


# ── Fail-safe: fehlend / stale ────────────────────────────────────────────────


def test_missing_snapshot_yields_no_evidence(tmp_path: Path) -> None:
    provider = build_hype_evidence_provider(_settings(tmp_path))
    assert provider is not None
    assert provider(_analysis(), _md(), SignalDirection.LONG) == ()


def test_stale_snapshot_yields_no_evidence_and_no_shadow_entry(tmp_path: Path) -> None:
    _seed(tmp_path, age_seconds=8000.0)  # > ttl 7200s default
    provider = build_hype_evidence_provider(_settings(tmp_path))
    assert provider is not None
    assert provider(_analysis(), _md(), SignalDirection.LONG) == ()
    assert _shadow_lines(tmp_path) == []


# ── Emission: LONG-Dämpfung ───────────────────────────────────────────────────


def test_overheated_long_emits_contra_evidence_and_shadow(tmp_path: Path) -> None:
    _seed(tmp_path, score=0.8)
    provider = build_hype_evidence_provider(_settings(tmp_path))
    assert provider is not None
    ev = provider(_analysis(), _md(), SignalDirection.LONG)
    assert len(ev) == 1
    assert ev[0].kind == EvidenceKind.SENTIMENT_OVERHEAT
    assert ev[0].direction_aligned == -1  # contra zum LONG-Signal
    assert ev[0].value == 0.8
    assert ev[0].source_trust == 0.5
    lines = _shadow_lines(tmp_path)
    assert len(lines) == 1
    assert lines[0]["evidence_emitted"] is True
    assert lines[0]["symbol"] == "BTC/USDT"


def test_pair_symbol_resolves_base_asset_snapshot(tmp_path: Path) -> None:
    _seed(tmp_path)  # Snapshot gekeyt als "BTC"
    provider = build_hype_evidence_provider(_settings(tmp_path))
    assert provider is not None
    assert len(provider(_analysis(), _md("BTC/USDT"), SignalDirection.LONG)) == 1
    assert provider(_analysis(), _md("ETH/USDT"), SignalDirection.LONG) == ()


# ── Emissions-Gate: Schwelle + insufficient_data ──────────────────────────────


def test_score_below_threshold_logs_shadow_but_emits_nothing(tmp_path: Path) -> None:
    _seed(tmp_path, score=0.2)  # < min_score_for_evidence 0.3
    provider = build_hype_evidence_provider(_settings(tmp_path))
    assert provider is not None
    assert provider(_analysis(), _md(), SignalDirection.LONG) == ()
    lines = _shadow_lines(tmp_path)
    assert len(lines) == 1
    assert lines[0]["evidence_emitted"] is False
    assert lines[0]["hype_score"] == 0.2


def test_insufficient_data_snapshot_never_emits(tmp_path: Path) -> None:
    _seed(tmp_path, score=0.8, insufficient=True)
    provider = build_hype_evidence_provider(_settings(tmp_path))
    assert provider is not None
    assert provider(_analysis(), _md(), SignalDirection.LONG) == ()
    assert _shadow_lines(tmp_path)[0]["evidence_emitted"] is False


# ── dampen_only-Sicherheitsvertrag (Shorts) ───────────────────────────────────


def test_short_with_dampen_only_emits_nothing_but_logs_shadow(tmp_path: Path) -> None:
    _seed(tmp_path, score=0.8)
    provider = build_hype_evidence_provider(_settings(tmp_path))  # dampen_only default True
    assert provider is not None
    assert provider(_analysis(), _md(), SignalDirection.SHORT) == ()
    lines = _shadow_lines(tmp_path)
    assert len(lines) == 1
    assert lines[0]["evidence_emitted"] is False
    assert lines[0]["evidence_direction_aligned"] == 0


def test_short_with_symmetric_mode_emits_pro_short_evidence(tmp_path: Path) -> None:
    _seed(tmp_path, score=0.8)
    provider = build_hype_evidence_provider(_settings(tmp_path, dampen_only=False))
    assert provider is not None
    ev = provider(_analysis(), _md(), SignalDirection.SHORT)
    assert len(ev) == 1
    assert ev[0].direction_aligned == 1  # contrarian: Überhitzung stützt SHORT


# ── Snapshot-Store-Robustheit ─────────────────────────────────────────────────


def test_store_roundtrip_and_base_asset_keying(tmp_path: Path) -> None:
    store = HypeSnapshotStore(tmp_path / "hype.json")
    store.write_many(
        [
            HypeSnapshot(
                asset="eth/usdt",  # unnormalisiert geschrieben
                timestamp_utc="2026-06-12T10:00:00+00:00",
                hype_score=0.5,
                velocity_ratio=2.0,
                mentions_recent=8,
                distinct_sources_recent=3,
                one_sidedness=0.5,
                insufficient_data=False,
            )
        ]
    )
    snap = store.read("ETH/USDT")
    assert snap is not None
    assert snap.hype_score == 0.5
    assert store.read("ETH") is snap or store.read("ETH") == snap


def test_store_corrupt_file_reads_empty(tmp_path: Path) -> None:
    path = tmp_path / "hype.json"
    path.write_text("{not json", encoding="utf-8")
    assert HypeSnapshotStore(path).read_all() == {}
