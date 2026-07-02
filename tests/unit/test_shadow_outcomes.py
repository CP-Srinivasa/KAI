"""Unit tests for the canonical fill-independent outcome loader."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.research.shadow_outcomes import (
    HORIZONS,
    build_outcomes,
    entry_ts_for,
    load_entry_times,
    parse_ts,
    read_jsonl,
    to_feature_outcomes,
)


def test_read_jsonl_missing_file_returns_empty(tmp_path):
    assert read_jsonl(tmp_path / "nope.jsonl") == []


def test_read_jsonl_skips_blank_and_corrupt_lines(tmp_path):
    p = tmp_path / "x.jsonl"
    p.write_text('{"a": 1}\n\nnot-json\n["not-a-dict"]\n{"b": 2}\n', encoding="utf-8")
    assert read_jsonl(p) == [{"a": 1}, {"b": 2}]


def test_parse_ts_assumes_utc_for_naive_and_rejects_junk():
    assert parse_ts("2026-07-01T00:00:00").tzinfo == UTC
    assert parse_ts("2026-07-01T00:00:00+00:00") is not None
    assert parse_ts("garbage") is None
    assert parse_ts(None) is None
    assert parse_ts(123) is None


def test_entry_ts_for_prefers_ledger_then_embedded_iso():
    ts = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    times = {"cyc_1": ts}
    assert entry_ts_for({"candidate_id": "cyc_1"}, times) == ts
    # tech-<SYM>-<iso> id carries its own time when the ledger has no row
    embedded = entry_ts_for({"candidate_id": "tech-BTC/USDT-2026-07-01T09:30:00+00:00"}, {})
    assert embedded == datetime(2026, 7, 1, 9, 30, tzinfo=UTC)
    assert entry_ts_for({"candidate_id": "cyc_unknown"}, {}) is None


def _resolved(cid, sym, side, **fwd):
    row = {"candidate_id": cid, "symbol": sym, "side": side}
    for h in HORIZONS:
        row[f"fwd_{h}s_bps"] = fwd.get(f"h{h}")
    return row


def test_build_outcomes_filters_and_time_orders():
    times = {
        "a": datetime(2026, 7, 1, 12, 0, tzinfo=UTC),
        "b": datetime(2026, 7, 1, 11, 0, tzinfo=UTC),
        "sent": datetime(2026, 7, 1, 10, 0, tzinfo=UTC),
        "empty": datetime(2026, 7, 1, 9, 0, tzinfo=UTC),
    }
    resolved = [
        _resolved("a", "BTC/USDT", "long", h60=10.0, h3600=25.0),
        _resolved("b", "ETH/USDT", "short", h900=-5.0),
        _resolved("sent", "X/USDT", "long", h60=9999.0),  # sentinel -> dropped
        _resolved("empty", "Y/USDT", "long"),  # all-None -> dropped
        _resolved("bad", "Z/USDT", "sideways", h60=1.0),  # bad side -> dropped
    ]
    out = build_outcomes(resolved, times)
    assert [o["symbol"] for o in out] == ["ETH/USDT", "BTC/USDT"]  # time-ordered
    btc = out[1]
    assert btc["side"] == "long"
    assert btc["fwd"][60] == 10.0
    assert btc["fwd"][3600] == 25.0
    assert btc["fwd"][300] is None


def test_build_outcomes_side_adjusted_values_preserved():
    times = {"a": datetime(2026, 7, 1, tzinfo=UTC)}
    out = build_outcomes([_resolved("a", "BTC/USDT", "short", h3600=-42.0)], times)
    assert out[0]["fwd"][3600] == -42.0  # sign preserved (already side-adjusted upstream)


def test_load_entry_times_maps_candidate_id():
    ledger = [
        {"candidate_id": "c1", "ts_utc": "2026-07-01T00:00:00+00:00"},
        {"candidate_id": "c2", "ts_utc": "bad"},
        {"ts_utc": "2026-07-01T00:00:00+00:00"},  # no id
    ]
    times = load_entry_times(ledger)
    assert set(times) == {"c1"}


def test_to_feature_outcomes_projects_horizon_and_iso_ts():
    outcomes = [
        {
            "symbol": "BTC/USDT",
            "side": "long",
            "entry_ts": datetime(2026, 7, 1, 12, 0, tzinfo=UTC),
            "fwd": {60: 1.0, 300: None, 900: None, 3600: 33.0},
        }
    ]
    feats = to_feature_outcomes(outcomes, horizon=3600)
    assert feats == [
        {"symbol": "BTC/USDT", "entry_ts": "2026-07-01T12:00:00+00:00", "net_bps": 33.0}
    ]
    # a horizon with no value is dropped (not emitted as None)
    assert to_feature_outcomes(outcomes, horizon=300) == []


def test_to_feature_outcomes_rejects_unknown_horizon():
    with pytest.raises(ValueError):
        to_feature_outcomes([], horizon=120)
