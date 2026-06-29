"""Tests for app.analysis.features.unlock_calendar — fail-soft read-only loader."""

from __future__ import annotations

import json
from pathlib import Path

from app.analysis.features.unlock_calendar import (
    load_unlock_calendar,
    read_generated_at,
)

_DAY = 86_400_000
_NOW = 1_700_000_000_000  # fixed reference "now" in ms


def _write(path: Path, doc: object) -> Path:
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


# --- fail-soft paths --------------------------------------------------------


def test_missing_file_returns_empty(tmp_path: Path) -> None:
    assert load_unlock_calendar(tmp_path / "nope.json", now_ms=_NOW) == []


def test_corrupt_json_returns_empty(tmp_path: Path) -> None:
    p = tmp_path / "events.json"
    p.write_text("{not valid json", encoding="utf-8")
    assert load_unlock_calendar(p, now_ms=_NOW) == []


def test_non_dict_doc_returns_empty(tmp_path: Path) -> None:
    p = _write(tmp_path / "events.json", [1, 2, 3])
    assert load_unlock_calendar(p, now_ms=_NOW) == []


def test_missing_tokens_key_returns_empty(tmp_path: Path) -> None:
    p = _write(tmp_path / "events.json", {"schema": 1})
    assert load_unlock_calendar(p, now_ms=_NOW) == []


def test_malformed_event_pair_is_skipped_not_crashed(tmp_path: Path) -> None:
    p = _write(
        tmp_path / "events.json",
        {
            "schema": 1,
            "tokens": {
                "BAD": {"max_supply": 1000.0, "events": [["x", "y"], [None]]},
                "OK": {"max_supply": 1000.0, "events": [[_NOW + _DAY, 50.0]]},
            },
        },
    )
    out = load_unlock_calendar(p, now_ms=_NOW)
    assert [e["symbol"] for e in out] == ["OK"]


# --- core selection logic ---------------------------------------------------


def test_picks_earliest_upcoming_and_filters_past(tmp_path: Path) -> None:
    p = _write(
        tmp_path / "events.json",
        {
            "schema": 1,
            "tokens": {
                "APT": {
                    "max_supply": 1_000.0,
                    "events": [
                        [_NOW - 5 * _DAY, 10.0],  # past → ignored
                        [_NOW + 10 * _DAY, 30.0],  # later upcoming
                        [_NOW + 2 * _DAY, 20.0],  # earliest upcoming
                    ],
                }
            },
        },
    )
    out = load_unlock_calendar(p, now_ms=_NOW)
    assert len(out) == 1
    e = out[0]
    assert e["symbol"] == "APT"
    assert e["event_ms"] == _NOW + 2 * _DAY
    assert e["days_until"] == 2.0
    assert e["amount_tokens"] == 20.0
    assert e["frac_of_max_supply"] == 0.02  # 20 / 1000


def test_token_with_only_past_events_is_omitted(tmp_path: Path) -> None:
    p = _write(
        tmp_path / "events.json",
        {"schema": 1, "tokens": {"OLD": {"max_supply": 100.0, "events": [[_NOW - _DAY, 5.0]]}}},
    )
    assert load_unlock_calendar(p, now_ms=_NOW) == []


def test_unknown_max_supply_yields_null_fraction(tmp_path: Path) -> None:
    p = _write(
        tmp_path / "events.json",
        {"schema": 1, "tokens": {"X": {"max_supply": None, "events": [[_NOW + _DAY, 5.0]]}}},
    )
    out = load_unlock_calendar(p, now_ms=_NOW)
    assert len(out) == 1
    assert out[0]["frac_of_max_supply"] is None
    assert out[0]["amount_tokens"] == 5.0


def test_results_sorted_soonest_first(tmp_path: Path) -> None:
    p = _write(
        tmp_path / "events.json",
        {
            "schema": 1,
            "tokens": {
                "FAR": {"max_supply": 1.0, "events": [[_NOW + 30 * _DAY, 1.0]]},
                "NEAR": {"max_supply": 1.0, "events": [[_NOW + 1 * _DAY, 1.0]]},
                "MID": {"max_supply": 1.0, "events": [[_NOW + 7 * _DAY, 1.0]]},
            },
        },
    )
    out = load_unlock_calendar(p, now_ms=_NOW)
    assert [e["symbol"] for e in out] == ["NEAR", "MID", "FAR"]


# --- read_generated_at (schema-2 staleness timestamp) -----------------------


def test_read_generated_at_returns_timestamp(tmp_path: Path) -> None:
    p = _write(
        tmp_path / "events.json",
        {"schema": 2, "generated_at": "2026-06-29T15:48:06+00:00", "tokens": {}},
    )
    assert read_generated_at(p) == "2026-06-29T15:48:06+00:00"


def test_read_generated_at_none_for_schema1_without_timestamp(tmp_path: Path) -> None:
    # An old schema-1 artifact carries no build time → unknown age → caller stales it.
    p = _write(tmp_path / "events.json", {"schema": 1, "tokens": {}})
    assert read_generated_at(p) is None


def test_read_generated_at_none_on_missing_or_corrupt(tmp_path: Path) -> None:
    assert read_generated_at(tmp_path / "nope.json") is None
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert read_generated_at(bad) is None


def test_read_generated_at_none_on_nonstring(tmp_path: Path) -> None:
    p = _write(tmp_path / "events.json", {"schema": 2, "generated_at": 12345, "tokens": {}})
    assert read_generated_at(p) is None
