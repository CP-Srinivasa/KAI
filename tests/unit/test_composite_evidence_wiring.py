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
from app.core.settings import (
    FundingEvidenceSettings,
    HypeEvidenceSettings,
    LongShortRatioEvidenceSettings,
    OpenInterestEvidenceSettings,
)
from app.market_data.models import (
    FundingRateSnapshot,
    LongShortRatioSnapshot,
    MarketDataPoint,
    OpenInterestSnapshot,
)
from app.signals.bayesian_confidence import EvidenceKind
from app.signals.composite_evidence_wiring import build_composite_evidence_provider
from app.signals.funding_snapshot_store import FundingSnapshotStore
from app.signals.funding_wiring import build_funding_evidence_provider
from app.signals.hype_snapshot_store import HypeSnapshot, HypeSnapshotStore
from app.signals.ls_snapshot_store import LongShortRatioSnapshotStore
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


def _ls_settings(tmp_path: Path, *, enabled: bool) -> LongShortRatioEvidenceSettings:
    return LongShortRatioEvidenceSettings(
        enabled=enabled,
        source_trust=0.5,
        snapshot_path=tmp_path / "ls.json",
        shadow_log_path=tmp_path / "ls_shadow.jsonl",
    )


def _seed_ls(tmp_path: Path) -> None:
    LongShortRatioSnapshotStore(tmp_path / "ls.json").write_many(
        [
            LongShortRatioSnapshot(
                symbol="BTC/USDT",
                timestamp_utc=datetime.now(UTC).isoformat(),
                long_account_ratio=0.70,
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


# ── Phase 3: 3-source composite — all 8 enable-combinations ───────────────────
# Beweist: die 4 bestehenden Funding/OI-Kombinationen regressen NICHT (gleiche
# Evidence-Kinds/Reihenfolge), und L/S häng deterministisch als dritte Quelle
# hinten an (Reihenfolge Funding → OI → LS).

_F = EvidenceKind.FUNDING_RATE
_O = EvidenceKind.OPEN_INTEREST
_L = EvidenceKind.LONG_SHORT_RATIO


@pytest.mark.parametrize(
    ("f_on", "o_on", "l_on", "expected_kinds"),
    [
        (False, False, False, None),  # nichts an → None
        (True, False, False, [_F]),  # nur Funding (Phase-1, unverändert)
        (False, True, False, [_O]),  # nur OI (Phase-2, unverändert)
        (False, False, True, [_L]),  # nur LS (Phase-3)
        (True, True, False, [_F, _O]),  # Funding+OI (Phase-1+2, unverändert)
        (True, False, True, [_F, _L]),  # Funding+LS
        (False, True, True, [_O, _L]),  # OI+LS
        (True, True, True, [_F, _O, _L]),  # alle drei, feste Reihenfolge
    ],
)
def test_all_eight_combinations(
    tmp_path: Path,
    f_on: bool,
    o_on: bool,
    l_on: bool,
    expected_kinds: list[EvidenceKind] | None,
) -> None:
    if f_on:
        _seed_funding(tmp_path)
    if o_on:
        _seed_oi(tmp_path)
    if l_on:
        _seed_ls(tmp_path)
    composite = build_composite_evidence_provider(
        _funding_settings(tmp_path, enabled=f_on),
        _oi_settings(tmp_path, enabled=o_on),
        _ls_settings(tmp_path, enabled=l_on),
    )
    if expected_kinds is None:
        assert composite is None
        return
    assert composite is not None
    ev = composite(_analysis(), _md(), SignalDirection.LONG)
    assert [e.kind for e in ev] == expected_kinds


def test_phase12_no_regression_with_ls_off(tmp_path: Path) -> None:
    # Mit explizit ausgeschalteter LS-Quelle ist der Funding+OI-Pfad
    # IDENTISCH zum Phase-2-Verhalten (byte-gleiche Evidence-Sequenz).
    _seed_funding(tmp_path)
    _seed_oi(tmp_path)
    with_ls_arg = build_composite_evidence_provider(
        _funding_settings(tmp_path, enabled=True),
        _oi_settings(tmp_path, enabled=True),
        _ls_settings(tmp_path, enabled=False),
    )
    legacy_2arg = build_composite_evidence_provider(
        _funding_settings(tmp_path, enabled=True),
        _oi_settings(tmp_path, enabled=True),
    )
    assert with_ls_arg is not None and legacy_2arg is not None
    ev_a = with_ls_arg(_analysis(), _md(), SignalDirection.LONG)
    ev_b = legacy_2arg(_analysis(), _md(), SignalDirection.LONG)
    assert [e.kind for e in ev_a] == [e.kind for e in ev_b] == [_F, _O]
    assert ev_a[0].value == pytest.approx(ev_b[0].value)
    assert ev_a[1].value == pytest.approx(ev_b[1].value)


def test_only_ls_on_does_not_regress(tmp_path: Path) -> None:
    # Nur LS → der unveränderte LS-Sub-Provider wird DIREKT durchgereicht
    # (keine Composite-Hülle), byte-identisch zum standalone-Provider.
    from app.signals.ls_wiring import build_ls_evidence_provider

    _seed_ls(tmp_path)
    ls = _ls_settings(tmp_path, enabled=True)
    composite = build_composite_evidence_provider(
        _funding_settings(tmp_path, enabled=False),
        _oi_settings(tmp_path, enabled=False),
        ls,
    )
    standalone = build_ls_evidence_provider(ls)
    assert composite is not None and standalone is not None
    comp_ev = composite(_analysis(), _md(), SignalDirection.LONG)
    std_ev = standalone(_analysis(), _md(), SignalDirection.LONG)
    assert len(comp_ev) == len(std_ev) == 1
    assert comp_ev[0].kind == std_ev[0].kind == EvidenceKind.LONG_SHORT_RATIO
    assert comp_ev[0].value == pytest.approx(std_ev[0].value)
    assert comp_ev[0].direction_aligned == std_ev[0].direction_aligned


# ── HYPE-S1: vierte Quelle — kein Regress der V5-Pfade ────────────────────────

_H = EvidenceKind.SENTIMENT_OVERHEAT


def _hype_settings(tmp_path: Path, *, enabled: bool) -> HypeEvidenceSettings:
    return HypeEvidenceSettings(
        enabled=enabled,
        source_trust=0.5,
        snapshot_path=tmp_path / "hype.json",
        shadow_log_path=tmp_path / "hype_shadow.jsonl",
        min_score_for_evidence=0.3,
    )


def _seed_hype(tmp_path: Path, *, score: float = 0.8) -> None:
    HypeSnapshotStore(tmp_path / "hype.json").write_many(
        [
            HypeSnapshot(
                asset="BTC",
                timestamp_utc=datetime.now(UTC).isoformat(),
                hype_score=score,
                velocity_ratio=4.0,
                mentions_recent=20,
                distinct_sources_recent=6,
                one_sidedness=0.9,
                insufficient_data=False,
            )
        ]
    )


def test_legacy_three_arg_call_unchanged_with_hype_param_absent(tmp_path: Path) -> None:
    # Aufrufer früherer Phasen (ohne hype_settings) → Verhalten exakt wie vor
    # HYPE-S1: alle drei V5-Quellen an, KEINE Hype-Evidence in der Kette.
    _seed_funding(tmp_path)
    _seed_oi(tmp_path)
    _seed_ls(tmp_path)
    composite = build_composite_evidence_provider(
        _funding_settings(tmp_path, enabled=True),
        _oi_settings(tmp_path, enabled=True),
        _ls_settings(tmp_path, enabled=True),
    )
    assert composite is not None
    ev = composite(_analysis(), _md(), SignalDirection.LONG)
    assert [e.kind for e in ev] == [_F, _O, _L]


def test_only_hype_on_passes_sub_provider_through(tmp_path: Path) -> None:
    # Nur Hype → der unveränderte Hype-Sub-Provider wird DIREKT durchgereicht.
    from app.signals.hype_wiring import build_hype_evidence_provider

    _seed_hype(tmp_path)
    hs = _hype_settings(tmp_path, enabled=True)
    composite = build_composite_evidence_provider(
        _funding_settings(tmp_path, enabled=False),
        _oi_settings(tmp_path, enabled=False),
        _ls_settings(tmp_path, enabled=False),
        hs,
    )
    standalone = build_hype_evidence_provider(hs)
    assert composite is not None and standalone is not None
    comp_ev = composite(_analysis(), _md(), SignalDirection.LONG)
    std_ev = standalone(_analysis(), _md(), SignalDirection.LONG)
    assert len(comp_ev) == len(std_ev) == 1
    assert comp_ev[0].kind == std_ev[0].kind == _H
    assert comp_ev[0].direction_aligned == std_ev[0].direction_aligned == -1


def test_all_four_on_chains_in_fixed_order(tmp_path: Path) -> None:
    _seed_funding(tmp_path)
    _seed_oi(tmp_path)
    _seed_ls(tmp_path)
    _seed_hype(tmp_path)
    composite = build_composite_evidence_provider(
        _funding_settings(tmp_path, enabled=True),
        _oi_settings(tmp_path, enabled=True),
        _ls_settings(tmp_path, enabled=True),
        _hype_settings(tmp_path, enabled=True),
    )
    assert composite is not None
    ev = composite(_analysis(), _md(), SignalDirection.LONG)
    assert [e.kind for e in ev] == [_F, _O, _L, _H]


def test_all_four_off_returns_none(tmp_path: Path) -> None:
    composite = build_composite_evidence_provider(
        _funding_settings(tmp_path, enabled=False),
        _oi_settings(tmp_path, enabled=False),
        _ls_settings(tmp_path, enabled=False),
        _hype_settings(tmp_path, enabled=False),
    )
    assert composite is None
