"""Unit tests for the historic regime lookup.

Covers the four failure modes that the trading-loop integration must
distinguish:
- ok                : snapshot is fresh and effective at target
- all_future        : target predates everything we have
- stale             : everything we have is older than max_age_seconds
- no_history /
  asset_unknown     : the JSONL doesn't exist or is empty

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


def test_missing_asset_file_returns_asset_unknown(tmp_path: Path) -> None:
    """No file at all = honest signal to caller to log the proxy fallback."""
    result = get_regime_at("DOGE", "2026-05-16T10:00:00Z", base_dir=tmp_path)
    assert result.reason == "asset_unknown"
    assert result.snapshot is None


def test_empty_file_returns_no_history(tmp_path: Path) -> None:
    """File exists but is empty (e.g. first-run race)."""
    (tmp_path / "btc_regime.jsonl").parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "btc_regime.jsonl").write_text("", encoding="utf-8")
    result = get_regime_at("BTC", "2026-05-16T10:00:00Z", base_dir=tmp_path)
    assert result.reason in ("no_history", "asset_unknown")
    assert result.snapshot is None


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
