"""Tests for app.learning.regime_lookup (Step 5)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.learning.regime_lookup import (
    REGIME_KEY_SEPARATOR,
    RegimeLookup,
    RegimeSnapshot,
)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def _snap(
    *,
    asset: str = "BTC",
    ts: str,
    regime: str = "breakout_up",
    vol_class: str = "vol_low",
    confidence: float = 1.0,
) -> dict:
    return {
        "asset": asset,
        "timestamp": ts,
        "regime": regime,
        "vol_class": vol_class,
        "confidence": confidence,
    }


# ─── Construction + Schema ────────────────────────────────────────────────────


def test_from_artifacts_loads_known_assets(tmp_path: Path) -> None:
    _write_jsonl(
        tmp_path / "btc_regime.jsonl",
        [
            _snap(ts="2026-05-13T06:00:00Z"),
            _snap(ts="2026-05-13T07:00:00Z", regime="chop_quiet", vol_class="vol_low"),
        ],
    )
    _write_jsonl(
        tmp_path / "eth_regime.jsonl",
        [_snap(asset="ETH", ts="2026-05-13T06:00:00Z", regime="trend_down")],
    )

    lookup = RegimeLookup.from_artifacts(tmp_path)

    assert lookup.assets == ["BTC", "ETH"]
    assert len(lookup) == 3


def test_missing_directory_returns_empty_lookup(tmp_path: Path) -> None:
    nowhere = tmp_path / "does_not_exist"
    lookup = RegimeLookup.from_artifacts(nowhere)
    assert lookup.assets == []
    assert len(lookup) == 0
    assert lookup.lookup("BTC", datetime(2026, 5, 13, tzinfo=UTC)) is None


def test_malformed_lines_are_skipped(tmp_path: Path) -> None:
    path = tmp_path / "btc_regime.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps(_snap(ts="2026-05-13T06:00:00Z")),
                "{not json",
                json.dumps(
                    {"asset": "BTC", "timestamp": "bad-ts", "regime": "x", "vol_class": "y"}
                ),
                json.dumps({"asset": "BTC"}),  # missing required fields
                json.dumps(_snap(ts="2026-05-13T07:00:00Z", regime="chop_quiet")),
            ]
        ) + "\n",
        encoding="utf-8",
    )
    lookup = RegimeLookup.from_artifacts(tmp_path)
    assert len(lookup) == 2


# ─── Lookup-Semantik ──────────────────────────────────────────────────────────


def test_lookup_returns_latest_entry_at_or_before_timestamp(tmp_path: Path) -> None:
    _write_jsonl(
        tmp_path / "btc_regime.jsonl",
        [
            _snap(ts="2026-05-13T06:00:00Z", regime="breakout_up"),
            _snap(ts="2026-05-13T07:00:00Z", regime="chop_quiet"),
            _snap(ts="2026-05-13T08:00:00Z", regime="trend_up"),
        ],
    )
    lookup = RegimeLookup.from_artifacts(tmp_path)

    # Genau auf einem Bucket-Boundary
    snap = lookup.lookup("BTC", datetime(2026, 5, 13, 7, 0, tzinfo=UTC))
    assert snap is not None
    assert snap.regime == "chop_quiet"

    # Mittendrin → nimmt den vorherigen
    snap = lookup.lookup("BTC", datetime(2026, 5, 13, 7, 30, tzinfo=UTC))
    assert snap is not None
    assert snap.regime == "chop_quiet"

    # Nach allen Snapshots → letzter
    snap = lookup.lookup("BTC", datetime(2026, 5, 13, 23, 0, tzinfo=UTC))
    assert snap is not None
    assert snap.regime == "trend_up"


def test_lookup_before_first_snapshot_returns_none(tmp_path: Path) -> None:
    _write_jsonl(
        tmp_path / "btc_regime.jsonl",
        [_snap(ts="2026-05-13T07:00:00Z")],
    )
    lookup = RegimeLookup.from_artifacts(tmp_path)
    assert lookup.lookup("BTC", datetime(2026, 5, 13, 6, 30, tzinfo=UTC)) is None


def test_lookup_unknown_asset_returns_none(tmp_path: Path) -> None:
    _write_jsonl(
        tmp_path / "btc_regime.jsonl",
        [_snap(ts="2026-05-13T06:00:00Z")],
    )
    lookup = RegimeLookup.from_artifacts(tmp_path)
    assert lookup.lookup("SOL", datetime(2026, 5, 13, 7, tzinfo=UTC)) is None


def test_lookup_accepts_trading_pair_symbol(tmp_path: Path) -> None:
    """Bayes-Journal schreibt "BTC/USDT", Regime-State nutzt "BTC"."""
    _write_jsonl(
        tmp_path / "btc_regime.jsonl",
        [_snap(ts="2026-05-13T06:00:00Z", regime="breakout_up", vol_class="vol_high")],
    )
    lookup = RegimeLookup.from_artifacts(tmp_path)
    snap = lookup.lookup("BTC/USDT", datetime(2026, 5, 13, 6, 30, tzinfo=UTC))
    assert snap is not None
    assert snap.regime == "breakout_up"


def test_lookup_handles_naive_datetime_as_utc(tmp_path: Path) -> None:
    _write_jsonl(
        tmp_path / "btc_regime.jsonl",
        [_snap(ts="2026-05-13T06:00:00Z")],
    )
    lookup = RegimeLookup.from_artifacts(tmp_path)
    naive = datetime(2026, 5, 13, 7, 0)
    snap = lookup.lookup("BTC", naive)
    assert snap is not None  # interpreted as UTC


def test_unsorted_input_jsonl_is_sorted_internally(tmp_path: Path) -> None:
    _write_jsonl(
        tmp_path / "btc_regime.jsonl",
        [
            _snap(ts="2026-05-13T08:00:00Z", regime="trend_up"),
            _snap(ts="2026-05-13T06:00:00Z", regime="breakout_up"),
            _snap(ts="2026-05-13T07:00:00Z", regime="chop_quiet"),
        ],
    )
    lookup = RegimeLookup.from_artifacts(tmp_path)
    snap = lookup.lookup("BTC", datetime(2026, 5, 13, 7, 30, tzinfo=UTC))
    assert snap is not None
    assert snap.regime == "chop_quiet"


# ─── Regime-Key-Format ────────────────────────────────────────────────────────


def test_regime_key_combines_regime_and_vol_class() -> None:
    snap = RegimeSnapshot(
        asset="BTC",
        timestamp_utc=datetime(2026, 5, 13, 6, 0, tzinfo=UTC),
        regime="breakout_up",
        vol_class="vol_low",
        confidence=1.0,
    )
    assert snap.regime_key == f"breakout_up{REGIME_KEY_SEPARATOR}vol_low"


def test_regime_key_helper_returns_none_on_miss(tmp_path: Path) -> None:
    _write_jsonl(
        tmp_path / "btc_regime.jsonl",
        [_snap(ts="2026-05-13T07:00:00Z")],
    )
    lookup = RegimeLookup.from_artifacts(tmp_path)
    assert lookup.regime_key("SOL", datetime(2026, 5, 13, 7, tzinfo=UTC)) is None
    assert (
        lookup.regime_key("BTC", datetime(2026, 5, 13, 6, tzinfo=UTC)) is None
    )  # before first


def test_regime_key_helper_returns_canonical_string(tmp_path: Path) -> None:
    _write_jsonl(
        tmp_path / "eth_regime.jsonl",
        [_snap(asset="ETH", ts="2026-05-13T07:00:00Z", regime="trend_down", vol_class="vol_high")],
    )
    lookup = RegimeLookup.from_artifacts(tmp_path)
    key = lookup.regime_key("ETH/USDT", datetime(2026, 5, 13, 8, tzinfo=UTC))
    assert key == "trend_down|vol_high"


# ─── Sanity ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "symbol,expected_asset",
    [
        ("BTC", "BTC"),
        ("btc", "BTC"),
        ("BTC/USDT", "BTC"),
        ("btc-usd", "BTC"),
        ("ETH:USDC", "ETH"),
    ],
)
def test_symbol_normalization_accepts_common_formats(
    tmp_path: Path, symbol: str, expected_asset: str
) -> None:
    _write_jsonl(
        tmp_path / f"{expected_asset.lower()}_regime.jsonl",
        [_snap(asset=expected_asset, ts="2026-05-13T06:00:00Z")],
    )
    lookup = RegimeLookup.from_artifacts(tmp_path)
    assert lookup.lookup(symbol, datetime(2026, 5, 13, 6, 30, tzinfo=UTC)) is not None
