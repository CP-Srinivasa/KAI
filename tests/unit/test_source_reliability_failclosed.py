"""FS-3 (#199) — source-reliability fail-CLOSED for trust boosts in eligibility.

A missing / corrupt / stale / empty source_reliability.json must NOT grant a
positive priority lift (trust boost). Demotes (negative modifiers) still apply —
penalising a known-bad source on slightly-old data is the safe direction. The
legacy/"unknown" bucket never grants a boost regardless of file freshness.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from app.alerts import eligibility as elig
from app.alerts.eligibility import (
    BLOCK_REASON_LOW_PRIORITY,
    evaluate_directional_eligibility,
    source_reliability_status,
)

_FRESH_OK = {
    "report_type": "source_reliability",
    "scores": {
        "boostsource": {
            "source_name": "boostsource",
            "tier": "trusted",
            "priority_modifier": 1,
            "n": 60,
        },
        "demotesource": {
            "source_name": "demotesource",
            "tier": "low",
            "priority_modifier": -2,
            "n": 60,
        },
        "unknown": {"source_name": "unknown", "tier": "trusted", "priority_modifier": 1, "n": 80},
    },
}


def _write_reliability(
    monkeypatch, tmp_path, payload: str | dict, *, age_seconds: float = 0.0
) -> Path:
    monitor = tmp_path.parent / "monitor"
    monitor.mkdir(exist_ok=True)
    f = monitor / "source_reliability.json"
    f.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")
    if age_seconds > 0:
        old = time.time() - age_seconds
        os.utime(f, (old, old))
    monkeypatch.chdir(tmp_path.parent)
    elig._invalidate_source_reliability_cache()
    return f


def _decide(priority: int, source_name: str):
    return evaluate_directional_eligibility(
        sentiment_label="bullish",
        affected_assets=["BTC/USDT"],
        sentiment_score=0.8,
        impact_score=0.7,
        title="Some legit news",
        actionable=True,
        priority=priority,
        source_name=source_name,
    )


def test_ok_fresh_applies_boost_and_demote(tmp_path, monkeypatch) -> None:
    _write_reliability(monkeypatch, tmp_path, _FRESH_OK)
    assert source_reliability_status() == "ok"
    # +1 boost lifts P7 → P8 → eligible
    assert _decide(7, "boostsource").directional_eligible is True
    # -2 demote drops P9 → P7 → blocked
    dem = _decide(9, "demotesource")
    assert dem.directional_eligible is False
    assert dem.directional_block_reason == BLOCK_REASON_LOW_PRIORITY
    elig._invalidate_source_reliability_cache()


def test_stale_suppresses_boost_but_keeps_demote(tmp_path, monkeypatch) -> None:
    _write_reliability(monkeypatch, tmp_path, _FRESH_OK, age_seconds=48 * 3600)
    assert source_reliability_status() == "stale"
    # boost SUPPRESSED → P7 stays P7 → blocked
    assert _decide(7, "boostsource").directional_eligible is False
    # demote still applies → P9 → P7 → blocked
    assert _decide(9, "demotesource").directional_eligible is False
    elig._invalidate_source_reliability_cache()


def test_missing_file_is_unavailable_no_boost(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path.parent)
    # ensure no file
    f = tmp_path.parent / "monitor" / "source_reliability.json"
    if f.exists():
        f.unlink()
    elig._invalidate_source_reliability_cache()
    assert source_reliability_status() == "unavailable"
    assert _decide(7, "boostsource").directional_eligible is False
    elig._invalidate_source_reliability_cache()


def test_corrupt_file_is_corrupt_no_boost(tmp_path, monkeypatch) -> None:
    _write_reliability(monkeypatch, tmp_path, "{not valid json}}")
    assert source_reliability_status() == "corrupt"
    assert _decide(7, "boostsource").directional_eligible is False
    elig._invalidate_source_reliability_cache()


def test_empty_scores_is_empty_no_boost(tmp_path, monkeypatch) -> None:
    _write_reliability(monkeypatch, tmp_path, {"report_type": "source_reliability", "scores": {}})
    assert source_reliability_status() == "empty"
    assert _decide(7, "boostsource").directional_eligible is False
    elig._invalidate_source_reliability_cache()


def test_legacy_unknown_never_boosts_even_when_ok(tmp_path, monkeypatch) -> None:
    _write_reliability(monkeypatch, tmp_path, _FRESH_OK)
    assert source_reliability_status() == "ok"
    # "unknown" carries +1 in the file but is the legacy bucket → stripped at load,
    # so P7 stays P7 → blocked.
    assert _decide(7, "unknown").directional_eligible is False
    elig._invalidate_source_reliability_cache()
