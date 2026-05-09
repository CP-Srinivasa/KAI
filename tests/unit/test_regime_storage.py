"""Tests for regime JSONL storage (append + load + latest)."""

from __future__ import annotations

from pathlib import Path

from app.regime.models import RegimeClass, RegimeSnapshot
from app.regime.storage import (
    append_regime_snapshot,
    latest_regime_snapshot,
    load_regime_snapshots,
    resolve_regime_path,
)


def _snap(
    asset: str = "BTC",
    ts: str = "2026-05-09T13:00:00Z",
    regime: RegimeClass = RegimeClass.TREND_UP,
    pending: RegimeClass | None = None,
    pending_n: int = 0,
) -> RegimeSnapshot:
    return RegimeSnapshot(
        asset=asset,
        timestamp=ts,
        regime=regime,
        vol_class="vol_normal",
        confidence=1.0,
        adx=42.5,
        plus_di=33.0,
        minus_di=4.0,
        rv_24h=0.05,
        atr_zscore=1.2,
        pending_regime=pending,
        pending_consecutive=pending_n,
    )


def test_resolve_regime_path_lowercases_asset(tmp_path: Path) -> None:
    p = resolve_regime_path("BTC", tmp_path)
    assert p == tmp_path / "btc_regime.jsonl"


def test_load_returns_empty_when_file_missing(tmp_path: Path) -> None:
    assert load_regime_snapshots("BTC", tmp_path) == []
    assert latest_regime_snapshot("BTC", tmp_path) is None


def test_append_creates_file_and_writes_line(tmp_path: Path) -> None:
    snap = _snap()
    out_path = append_regime_snapshot(snap, tmp_path)
    assert out_path.exists()
    content = out_path.read_text(encoding="utf-8")
    assert content.count("\n") == 1
    assert "trend_up" in content


def test_append_preserves_order_oldest_first(tmp_path: Path) -> None:
    s1 = _snap(ts="2026-05-09T13:00:00Z", regime=RegimeClass.TREND_UP)
    s2 = _snap(ts="2026-05-09T14:00:00Z", regime=RegimeClass.CHOP_QUIET)
    s3 = _snap(ts="2026-05-09T15:00:00Z", regime=RegimeClass.TREND_DOWN)
    append_regime_snapshot(s1, tmp_path)
    append_regime_snapshot(s2, tmp_path)
    append_regime_snapshot(s3, tmp_path)

    loaded = load_regime_snapshots("BTC", tmp_path)
    assert [s.timestamp for s in loaded] == [
        "2026-05-09T13:00:00Z",
        "2026-05-09T14:00:00Z",
        "2026-05-09T15:00:00Z",
    ]
    assert [s.regime for s in loaded] == [
        RegimeClass.TREND_UP,
        RegimeClass.CHOP_QUIET,
        RegimeClass.TREND_DOWN,
    ]


def test_latest_returns_most_recent_line(tmp_path: Path) -> None:
    s1 = _snap(ts="2026-05-09T13:00:00Z", regime=RegimeClass.TREND_UP)
    s2 = _snap(ts="2026-05-09T14:00:00Z", regime=RegimeClass.CHOP_QUIET)
    append_regime_snapshot(s1, tmp_path)
    append_regime_snapshot(s2, tmp_path)
    latest = latest_regime_snapshot("BTC", tmp_path)
    assert latest is not None
    assert latest.timestamp == "2026-05-09T14:00:00Z"
    assert latest.regime == RegimeClass.CHOP_QUIET


def test_roundtrip_preserves_indicator_values_and_pending(tmp_path: Path) -> None:
    snap = _snap(
        regime=RegimeClass.TREND_UP,
        pending=RegimeClass.CHOP_VOLATILE,
        pending_n=1,
    )
    append_regime_snapshot(snap, tmp_path)
    loaded = load_regime_snapshots("BTC", tmp_path)
    assert len(loaded) == 1
    out = loaded[0]
    assert out.asset == "BTC"
    assert out.regime == RegimeClass.TREND_UP
    assert out.pending_regime == RegimeClass.CHOP_VOLATILE
    assert out.pending_consecutive == 1
    assert out.adx == 42.5
    assert out.plus_di == 33.0
    assert out.minus_di == 4.0
    assert out.rv_24h == 0.05
    assert out.atr_zscore == 1.2


def test_load_skips_malformed_lines(tmp_path: Path) -> None:
    p = resolve_regime_path("BTC", tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        "{not valid json\n"
        '{"asset":"BTC","timestamp":"2026-05-09T13:00:00Z","regime":"trend_up","vol_class":"vol_normal","confidence":1.0}\n'
        "\n"
        '{"asset":"BTC","timestamp":"2026-05-09T14:00:00Z","regime":"INVALID_REGIME","vol_class":"vol_normal","confidence":1.0}\n'
        '{"asset":"BTC","timestamp":"2026-05-09T15:00:00Z","regime":"chop_quiet","vol_class":"vol_low","confidence":1.0}\n',
        encoding="utf-8",
    )
    loaded = load_regime_snapshots("BTC", tmp_path)
    # Lines: malformed-json (skip), valid (keep), empty (skip),
    # invalid-regime (skip), valid (keep).
    assert len(loaded) == 2
    assert loaded[0].timestamp == "2026-05-09T13:00:00Z"
    assert loaded[1].timestamp == "2026-05-09T15:00:00Z"


def test_separate_assets_use_separate_files(tmp_path: Path) -> None:
    btc = _snap(asset="BTC", regime=RegimeClass.TREND_UP)
    eth = _snap(asset="ETH", regime=RegimeClass.CHOP_QUIET)
    append_regime_snapshot(btc, tmp_path)
    append_regime_snapshot(eth, tmp_path)

    btc_loaded = load_regime_snapshots("BTC", tmp_path)
    eth_loaded = load_regime_snapshots("ETH", tmp_path)
    assert len(btc_loaded) == 1
    assert len(eth_loaded) == 1
    assert btc_loaded[0].regime == RegimeClass.TREND_UP
    assert eth_loaded[0].regime == RegimeClass.CHOP_QUIET
