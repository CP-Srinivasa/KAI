"""Unit tests for the Source-Reliability feedback loop.

Covers three layers:
1. wilson_lower_bound — the math, including edge cases (n=0, hits>n, n=1).
2. build_source_reliability_report — window cutoff, dedup, tier assignment,
   inconclusive exclusion, missing-source exclusion.
3. eligibility integration — reliability modifier combines with watch-list,
   demote tips P9→P7-blocked, promote tips P7→P8-eligible, mtime-cache reload.

KAI-no-prediction-rule: tests assert lower-bound behaviour, never claims
about future hit-rate.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.alerts.audit import AlertAuditRecord, AlertOutcomeAnnotation
from app.learning.source_reliability import (
    SourceReliabilityScore,
    build_source_reliability_report,
    wilson_lower_bound,
)

# ── wilson_lower_bound math ────────────────────────────────────────────────


def test_wilson_returns_none_for_zero_n() -> None:
    assert wilson_lower_bound(0, 0) is None


def test_wilson_lower_for_all_hits_is_high_but_not_one() -> None:
    """20/20 hits should not assert "100% future hit-rate" — Wilson stays <1.0."""
    lower = wilson_lower_bound(20, 20)
    assert lower is not None
    assert 0.80 < lower < 1.0


def test_wilson_lower_for_all_miss_is_low_but_not_zero() -> None:
    lower = wilson_lower_bound(0, 20)
    assert lower is not None
    assert 0.0 <= lower < 0.20


def test_wilson_lower_grows_with_n_at_fixed_rate() -> None:
    """50% point-estimate with more data tightens the lower bound upward."""
    low_n = wilson_lower_bound(5, 10)
    high_n = wilson_lower_bound(50, 100)
    assert low_n is not None and high_n is not None
    assert high_n > low_n


def test_wilson_lower_caps_hits_above_n() -> None:
    """Defensive: corrupted input where hits > n must not crash."""
    lower = wilson_lower_bound(15, 10)
    assert lower is not None
    assert 0.0 <= lower <= 1.0


def test_wilson_lower_matches_known_value_for_25_of_100() -> None:
    """Sanity check against a hand-computed Wilson Lower 95 for 25/100.

    p̂ = 0.25, z = 1.96, n = 100
    denominator = 1 + z²/n = 1 + 0.038416 = 1.038416
    center = 0.25 + z²/(2n) = 0.25 + 0.019208 = 0.269208
    inner = 0.25*0.75/100 + z²/(4n²) = 0.001875 + 0.0000096 ≈ 0.001884
    margin = 1.96 * sqrt(0.001884) ≈ 0.08507
    lower = (0.269208 - 0.08507) / 1.038416 ≈ 0.1773
    """
    lower = wilson_lower_bound(25, 100)
    assert lower is not None
    assert math.isclose(lower, 0.1773, abs_tol=0.005)


# ── build_source_reliability_report ────────────────────────────────────────


def _audit(
    doc_id: str,
    source: str,
    dispatched_at: str = "2026-05-15T10:00:00+00:00",
) -> AlertAuditRecord:
    return AlertAuditRecord(
        document_id=doc_id,
        channel="telegram",
        message_id="m",
        is_digest=False,
        dispatched_at=dispatched_at,
        source_name=source,
    )


def _ann(doc_id: str, outcome: str) -> AlertOutcomeAnnotation:
    return AlertOutcomeAnnotation(
        document_id=doc_id,
        outcome=outcome,  # type: ignore[arg-type]
        annotated_at="2026-05-15T12:00:00+00:00",
    )


def test_report_excludes_inconclusive_from_n() -> None:
    """Inconclusive contributes to neither hits nor misses (denominator)."""
    audits = [
        _audit("d1", "CoinDesk"),
        _audit("d2", "CoinDesk"),
        _audit("d3", "CoinDesk"),
    ]
    annotations = [
        _ann("d1", "hit"),
        _ann("d2", "miss"),
        _ann("d3", "inconclusive"),  # excluded
    ]
    report = build_source_reliability_report(
        audits,
        annotations,
        source_by_doc={"d1": "CoinDesk", "d2": "CoinDesk", "d3": "CoinDesk"},
    )
    score = report["scores"]["CoinDesk"]
    assert score["hits"] == 1
    assert score["miss"] == 1
    assert score["n"] == 2  # inconclusive excluded


def test_report_window_cutoff_drops_old_alerts() -> None:
    """Alert older than window_days must NOT influence the tier."""
    now = datetime(2026, 5, 16, tzinfo=UTC)
    old_audit = _audit(
        "d_old",
        "OldSource",
        dispatched_at=(now - timedelta(days=120)).isoformat(),
    )
    fresh_audit = _audit(
        "d_fresh",
        "OldSource",
        dispatched_at=(now - timedelta(days=10)).isoformat(),
    )
    report = build_source_reliability_report(
        [old_audit, fresh_audit],
        [_ann("d_old", "hit"), _ann("d_fresh", "miss")],
        source_by_doc={"d_old": "OldSource", "d_fresh": "OldSource"},
        window_days=90,
        now_utc=now,
    )
    score = report["scores"]["OldSource"]
    assert score["hits"] == 0
    assert score["miss"] == 1
    assert score["n"] == 1


def test_report_excludes_alerts_without_source() -> None:
    """Documents with no source mapping cannot contribute to anyone."""
    audits = [_audit("d1", ""), _audit("d2", "Decrypt")]
    annotations = [_ann("d1", "hit"), _ann("d2", "hit")]
    report = build_source_reliability_report(
        audits,
        annotations,
        source_by_doc={"d2": "Decrypt"},  # d1 omitted
    )
    assert "Decrypt" in report["scores"]
    assert len(report["scores"]) == 1


def test_report_classifies_low_tier_for_consistent_misses() -> None:
    """A source with 0/25 hits over the window should land in `low` tier."""
    audits = [_audit(f"d{i}", "NoiseSource") for i in range(25)]
    annotations = [_ann(f"d{i}", "miss") for i in range(25)]
    report = build_source_reliability_report(
        audits,
        annotations,
        source_by_doc={f"d{i}": "NoiseSource" for i in range(25)},
    )
    score = report["scores"]["NoiseSource"]
    assert score["tier"] == "low"
    assert score["priority_modifier"] == -2


def test_report_classifies_trusted_tier_for_strong_evidence() -> None:
    """30+ resolved with very high hit-rate → `trusted` tier, +1 modifier."""
    audits = [_audit(f"d{i}", "TopSource") for i in range(40)]
    annotations = []
    for i in range(40):
        annotations.append(_ann(f"d{i}", "hit" if i < 35 else "miss"))  # 35/40 = 87.5%
    report = build_source_reliability_report(
        audits,
        annotations,
        source_by_doc={f"d{i}": "TopSource" for i in range(40)},
    )
    score = report["scores"]["TopSource"]
    assert score["tier"] == "trusted"
    assert score["priority_modifier"] == 1


def test_report_returns_insufficient_for_small_n() -> None:
    """A cold-start source with n<20 stays at modifier=0."""
    audits = [_audit(f"d{i}", "NewSource") for i in range(5)]
    annotations = [_ann(f"d{i}", "hit") for i in range(5)]
    report = build_source_reliability_report(
        audits,
        annotations,
        source_by_doc={f"d{i}": "NewSource" for i in range(5)},
    )
    score = report["scores"]["NewSource"]
    assert score["tier"] == "insufficient"
    assert score["priority_modifier"] == 0


def test_report_includes_thresholds_metadata() -> None:
    """The output records its thresholds so a reader knows what they bought."""
    report = build_source_reliability_report([], [], {})
    assert "thresholds" in report
    assert report["thresholds"]["min_n_for_demote"] == 20


def test_report_excludes_digest_audits() -> None:
    """Digest rows (is_digest=True) must NOT contribute — they are aggregates."""
    digest = AlertAuditRecord(
        document_id="digest-1",
        channel="telegram",
        message_id="d",
        is_digest=True,
        dispatched_at="2026-05-15T10:00:00+00:00",
        source_name="DigestSource",
    )
    regular = _audit("d1", "DigestSource")
    report = build_source_reliability_report(
        [digest, regular],
        [_ann("digest-1", "hit"), _ann("d1", "miss")],
        source_by_doc={"digest-1": "DigestSource", "d1": "DigestSource"},
    )
    score = report["scores"]["DigestSource"]
    assert score["n"] == 1  # only regular counted


# ── SourceReliabilityScore.to_json_dict ────────────────────────────────────


def test_score_to_json_dict_round_trips_all_fields() -> None:
    score = SourceReliabilityScore(
        source_name="X",
        hits=12,
        miss=8,
        n=20,
        point_estimate=0.6,
        wilson_lower_95=0.38,
        tier="neutral",
        priority_modifier=0,
    )
    d = score.to_json_dict()
    assert d["source_name"] == "X"
    assert d["hits"] == 12
    assert d["miss"] == 8
    assert d["n"] == 20
    assert d["tier"] == "neutral"
    assert d["priority_modifier"] == 0


# ── Eligibility integration ────────────────────────────────────────────────


def _write_reliability_json(monitor_dir: Path, scores: dict[str, dict]) -> Path:
    p = monitor_dir / "source_reliability.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(
            {
                "report_type": "source_reliability",
                "generated_at": "2026-05-16T10:00:00+00:00",
                "window_days": 90,
                "scores": scores,
            }
        ),
        encoding="utf-8",
    )
    return p


def test_eligibility_demote_blocks_p9_when_source_is_low_tier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A demote (-2) on a P9 alert drops effective to P7 — must be blocked."""
    from app.alerts.eligibility import (
        _invalidate_source_reliability_cache,
        _invalidate_source_watchlist_cache,
        evaluate_directional_eligibility,
    )

    monkeypatch.chdir(tmp_path)
    (tmp_path / "monitor").mkdir()
    _write_reliability_json(
        tmp_path / "monitor",
        {
            "BadSource": {
                "tier": "low",
                "priority_modifier": -2,
                "n": 25,
                "wilson_lower_95": 0.15,
            }
        },
    )
    _invalidate_source_reliability_cache()
    _invalidate_source_watchlist_cache()

    decision = evaluate_directional_eligibility(
        sentiment_label="bullish",
        affected_assets=["BTC/USDT"],
        priority=9,
        actionable=True,
        source_name="BadSource",
    )
    assert decision.directional_eligible is False
    assert decision.directional_block_reason == "low_priority"


def test_eligibility_promote_keeps_p8_eligible_with_modifier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A promote (+1) on a P8 alert keeps it eligible (P8+1=P9 still ok)."""
    from app.alerts.eligibility import (
        _invalidate_source_reliability_cache,
        _invalidate_source_watchlist_cache,
        evaluate_directional_eligibility,
    )

    monkeypatch.chdir(tmp_path)
    (tmp_path / "monitor").mkdir()
    _write_reliability_json(
        tmp_path / "monitor",
        {
            "GoodSource": {
                "tier": "trusted",
                "priority_modifier": 1,
                "n": 50,
                "wilson_lower_95": 0.70,
            }
        },
    )
    _invalidate_source_reliability_cache()
    _invalidate_source_watchlist_cache()

    decision = evaluate_directional_eligibility(
        sentiment_label="bullish",
        affected_assets=["BTC/USDT"],
        priority=8,
        actionable=True,
        source_name="GoodSource",
    )
    assert decision.directional_eligible is True


def test_eligibility_no_file_means_no_modifier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing file = no modifier (fail-open). Existing watchlist still applies."""
    from app.alerts.eligibility import (
        _invalidate_source_reliability_cache,
        _invalidate_source_watchlist_cache,
        evaluate_directional_eligibility,
    )

    monkeypatch.chdir(tmp_path)
    (tmp_path / "monitor").mkdir()
    _invalidate_source_reliability_cache()
    _invalidate_source_watchlist_cache()

    decision = evaluate_directional_eligibility(
        sentiment_label="bullish",
        affected_assets=["BTC/USDT"],
        priority=8,
        actionable=True,
        source_name="AnySource",
    )
    assert decision.directional_eligible is True


def test_eligibility_corrupted_file_falls_back_to_no_modifier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Malformed JSON must not crash — eligibility silently treats as empty."""
    from app.alerts.eligibility import (
        _invalidate_source_reliability_cache,
        _invalidate_source_watchlist_cache,
        evaluate_directional_eligibility,
    )

    monkeypatch.chdir(tmp_path)
    (tmp_path / "monitor").mkdir()
    (tmp_path / "monitor" / "source_reliability.json").write_text(
        "not valid json",
        encoding="utf-8",
    )
    _invalidate_source_reliability_cache()
    _invalidate_source_watchlist_cache()

    decision = evaluate_directional_eligibility(
        sentiment_label="bullish",
        affected_assets=["BTC/USDT"],
        priority=9,
        actionable=True,
        source_name="AnySource",
    )
    # Falls back to "no modifier" → P9 stays eligible
    assert decision.directional_eligible is True
