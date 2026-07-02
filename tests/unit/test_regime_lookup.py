"""Unit tests for the historic regime lookup.

Covers the failure modes that the trading-loop integration must
distinguish (collapsed pre-2026-05-26, split after):
- ok                  : snapshot is fresh and effective at target
- all_future          : target predates everything we have
- stale               : everything we have is older than max_age_seconds
- no_snapshot_file    : asset file absent (storage drift / wrong CWD)
- no_snapshots_data   : file exists but no parseable snapshot
- asset_unsupported   : asset outside R1 coverage (BTC + ETH)
- invalid_timestamp   : target string cannot be parsed

Plus the no-look-ahead invariant (lookup must NEVER return a snapshot
whose timestamp is greater than the target) and the same-hour-write
contract (a later write at the same timestamp wins).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.regime.lookup import (
    RegimeLookupResult,
    get_regime_at,
    symbol_to_regime_asset,
)


def _write_jsonl(
    base_dir: Path,
    asset: str,
    snapshots: list[dict[str, object]],
) -> Path:
    """Write minimal regime-snapshot lines for an asset under base_dir."""
    p = base_dir / f"{asset.lower()}_regime.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as fh:
        for snap in snapshots:
            fh.write(json.dumps(snap) + "\n")
    return p


def _make_snap(timestamp: str, regime: str = "trend_up", **kwargs: object) -> dict:
    """Build a regime-snapshot dict in the storage-layer's expected shape."""
    base: dict[str, object] = {
        "asset": kwargs.pop("asset", "BTC"),
        "timestamp": timestamp,
        "regime": regime,
        "vol_class": "vol_low",
        "confidence": 1.0,
    }
    base.update(kwargs)
    return base


def test_returns_ok_for_exact_timestamp_match(tmp_path: Path) -> None:
    _write_jsonl(
        tmp_path,
        "BTC",
        [
            _make_snap("2026-05-16T09:00:00Z", regime="trend_up"),
            _make_snap("2026-05-16T10:00:00Z", regime="breakout_up"),
            _make_snap("2026-05-16T11:00:00Z", regime="chop_quiet"),
        ],
    )
    result = get_regime_at("BTC", "2026-05-16T10:00:00Z", base_dir=tmp_path)
    assert result.reason == "ok"
    assert result.snapshot is not None
    assert result.snapshot.regime == "breakout_up"
    assert result.age_seconds == 0.0


def test_returns_largest_le_for_between_timestamps(tmp_path: Path) -> None:
    """Target is in the middle of an hour — return the on-hour snapshot.

    A trade cycle at 10:23 should see the 10:00 regime, not the 11:00.
    """
    _write_jsonl(
        tmp_path,
        "BTC",
        [
            _make_snap("2026-05-16T09:00:00Z", regime="trend_up"),
            _make_snap("2026-05-16T10:00:00Z", regime="breakout_up"),
            _make_snap("2026-05-16T11:00:00Z", regime="chop_quiet"),
        ],
    )
    result = get_regime_at("BTC", "2026-05-16T10:23:45Z", base_dir=tmp_path)
    assert result.reason == "ok"
    assert result.snapshot is not None
    assert result.snapshot.regime == "breakout_up"
    assert result.age_seconds == pytest.approx(23 * 60 + 45, rel=1e-6)


def test_no_lookahead_returns_all_future(tmp_path: Path) -> None:
    """Target before first snapshot must NOT return that future snapshot."""
    _write_jsonl(
        tmp_path,
        "BTC",
        [
            _make_snap("2026-05-16T12:00:00Z", regime="trend_up"),
            _make_snap("2026-05-16T13:00:00Z", regime="breakout_up"),
        ],
    )
    result = get_regime_at("BTC", "2026-05-16T10:00:00Z", base_dir=tmp_path)
    assert result.reason == "all_future"
    assert result.snapshot is None


def test_stale_when_only_old_snapshots_exist(tmp_path: Path) -> None:
    """If the newest le-target snapshot is older than max_age, refuse."""
    _write_jsonl(
        tmp_path,
        "BTC",
        [
            _make_snap("2026-05-15T08:00:00Z", regime="trend_up"),
        ],
    )
    result = get_regime_at(
        "BTC",
        "2026-05-16T10:00:00Z",
        base_dir=tmp_path,
        max_age_seconds=3600,  # 1h
    )
    assert result.reason == "stale"
    assert result.snapshot is None
    assert result.age_seconds is not None
    assert result.age_seconds > 3600


def test_unsupported_asset_returns_asset_unsupported(tmp_path: Path) -> None:
    """R1 covers BTC + ETH; anything else is an honest "asset_unsupported"
    so the caller can decide to drop to the BTC proxy explicitly."""
    result = get_regime_at("DOGE", "2026-05-16T10:00:00Z", base_dir=tmp_path)
    assert result.reason == "asset_unsupported"
    assert result.snapshot is None


def test_missing_btc_file_returns_no_snapshot_file(tmp_path: Path) -> None:
    """Supported asset (BTC) but file absent = infrastructure drift, not
    coverage gap. Regression 2026-05-26: workstation had no
    artifacts/regime_state/, lookup collapsed file-missing into the same
    "asset_unknown" reason as DOGE — operators could not distinguish a
    storage gap from a coverage gap."""
    result = get_regime_at("BTC", "2026-05-16T10:00:00Z", base_dir=tmp_path)
    assert result.reason == "no_snapshot_file"
    assert result.snapshot is None


def test_empty_file_returns_no_snapshots_data(tmp_path: Path) -> None:
    """File exists but contains zero parseable snapshots (e.g. first-run
    race or a write-then-crash sequence)."""
    p = tmp_path / "btc_regime.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("", encoding="utf-8")
    result = get_regime_at("BTC", "2026-05-16T10:00:00Z", base_dir=tmp_path)
    assert result.reason == "no_snapshots_data"
    assert result.snapshot is None


def test_eth_supported_path(tmp_path: Path) -> None:
    """ETH is the second R1-supported asset — same path as BTC."""
    p = tmp_path / "eth_regime.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("", encoding="utf-8")
    result = get_regime_at("ETH", "2026-05-16T10:00:00Z", base_dir=tmp_path)
    assert result.reason == "no_snapshots_data"


def test_empty_asset_string_returns_asset_unsupported(tmp_path: Path) -> None:
    result = get_regime_at("", "2026-05-16T10:00:00Z", base_dir=tmp_path)
    assert result.reason == "asset_unsupported"


def test_same_hour_rewrite_last_wins(tmp_path: Path) -> None:
    """A re-run within the same hour: storage-layer contract = last wins."""
    _write_jsonl(
        tmp_path,
        "BTC",
        [
            _make_snap("2026-05-16T10:00:00Z", regime="trend_up"),
            _make_snap("2026-05-16T10:00:00Z", regime="breakout_up"),  # later write
        ],
    )
    result = get_regime_at("BTC", "2026-05-16T10:00:00Z", base_dir=tmp_path)
    assert result.reason == "ok"
    assert result.snapshot is not None
    assert result.snapshot.regime == "breakout_up"


def test_invalid_timestamp_returns_invalid(tmp_path: Path) -> None:
    _write_jsonl(tmp_path, "BTC", [_make_snap("2026-05-16T10:00:00Z")])
    result = get_regime_at("BTC", "not-a-timestamp", base_dir=tmp_path)
    assert result.reason == "invalid_timestamp"
    assert result.snapshot is None


def test_corrupted_line_does_not_crash(tmp_path: Path) -> None:
    """A malformed JSONL line is skipped by storage.load_regime_snapshots,
    not raised — the lookup must still resolve from the surviving lines."""
    p = tmp_path / "btc_regime.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(_make_snap("2026-05-16T09:00:00Z", regime="trend_up"))
        + "\n"
        + "{this is not valid json\n"
        + json.dumps(_make_snap("2026-05-16T10:00:00Z", regime="breakout_up"))
        + "\n",
        encoding="utf-8",
    )
    result = get_regime_at("BTC", "2026-05-16T10:30:00Z", base_dir=tmp_path)
    assert result.reason == "ok"
    assert result.snapshot is not None
    assert result.snapshot.regime == "breakout_up"


# ── symbol_to_regime_asset ─────────────────────────────────────────────


@pytest.mark.parametrize(
    "symbol,expected",
    [
        ("BTC/USDT", "BTC"),
        ("BTC", "BTC"),
        ("XBT/USDT", "BTC"),  # BitMEX alias
        ("ETH/USDT", "ETH"),
        ("ETH", "ETH"),
        ("SOL/USDT", "BTC"),  # proxy fallback
        ("DOGE/USDT", "BTC"),
        ("ASTER/USDT", "BTC"),
        ("BTC-USD", "BTC"),  # alt separator
        ("eth-usd", "ETH"),  # lower-case
        ("", "BTC"),
        ("???", "BTC"),
    ],
)
def test_symbol_to_regime_asset(symbol: str, expected: str) -> None:
    assert symbol_to_regime_asset(symbol) == expected


# ── return-type contract ───────────────────────────────────────────────


def test_result_dataclass_is_frozen(tmp_path: Path) -> None:
    _write_jsonl(tmp_path, "BTC", [_make_snap("2026-05-16T10:00:00Z")])
    result = get_regime_at("BTC", "2026-05-16T10:00:00Z", base_dir=tmp_path)
    with pytest.raises((AttributeError, TypeError)):
        # frozen=True → mutating fields raises
        result.reason = "tampered"  # type: ignore[misc]


def test_result_is_regime_lookup_result_instance(tmp_path: Path) -> None:
    """Stable public-API contract: callers can isinstance-check the result."""
    _write_jsonl(tmp_path, "BTC", [_make_snap("2026-05-16T10:00:00Z")])
    result = get_regime_at("BTC", "2026-05-16T10:00:00Z", base_dir=tmp_path)
    assert isinstance(result, RegimeLookupResult)
