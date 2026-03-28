"""Tests for alert hit-rate computation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.alerts.audit import (
    AlertAuditRecord,
    AlertOutcomeAnnotation,
    append_alert_audit,
    append_outcome_annotation,
    load_alert_audits,
    load_outcome_annotations,
)
from app.alerts.hit_rate import (
    AlertOutcome,
    build_outcomes_from_records,
    classify_hit,
    compute_hit_rate,
)

# ── classify_hit ──────────────────────────────────────────────────────


def test_classify_hit_bullish_up():
    assert classify_hit("bullish", 100.0, 110.0) is True


def test_classify_hit_bullish_down():
    assert classify_hit("bullish", 100.0, 90.0) is False


def test_classify_hit_bearish_down():
    assert classify_hit("bearish", 100.0, 90.0) is True


def test_classify_hit_bearish_up():
    assert classify_hit("bearish", 100.0, 110.0) is False


def test_classify_hit_bullish_flat():
    """Flat price is not a hit for bullish."""
    assert classify_hit("bullish", 100.0, 100.0) is False


def test_classify_hit_bearish_flat():
    """Flat price is not a hit for bearish."""
    assert classify_hit("bearish", 100.0, 100.0) is False


# ── build_outcomes_from_records ────────────────────────────────────────


def _make_record(
    doc_id: str = "doc-1",
    sentiment: str = "bullish",
    assets: list[str] | None = None,
    dispatched_at: str = "2026-01-01T12:00:00+00:00",
) -> AlertAuditRecord:
    return AlertAuditRecord(
        document_id=doc_id,
        channel="telegram",
        message_id="msg-1",
        is_digest=False,
        dispatched_at=dispatched_at,
        sentiment_label=sentiment,
        affected_assets=assets or ["BTC"],
        priority=8,
        actionable=True,
    )


def test_build_outcomes_filters_neutral():
    records = [
        _make_record(sentiment="neutral"),
        _make_record(sentiment="mixed"),
        _make_record(sentiment="bullish"),
    ]
    outcomes = build_outcomes_from_records(records)
    assert len(outcomes) == 1
    assert outcomes[0].sentiment_label == "bullish"


def test_build_outcomes_no_price_data():
    records = [_make_record()]
    outcomes = build_outcomes_from_records(records)
    assert len(outcomes) == 1
    assert outcomes[0].is_hit is None
    assert outcomes[0].price_at_alert is None


def test_build_outcomes_with_price_data():
    records = [_make_record(assets=["BTC"])]
    lookup = {("BTC", "2026-01-01T12:00:00+00:00"): (40000.0, 42000.0, "2026-01-02T12:00:00+00:00")}
    outcomes = build_outcomes_from_records(records, price_lookup=lookup)
    assert len(outcomes) == 1
    assert outcomes[0].is_hit is True
    assert outcomes[0].return_pct == 5.0


def test_build_outcomes_multi_asset():
    records = [_make_record(assets=["BTC", "ETH"])]
    outcomes = build_outcomes_from_records(records)
    assert len(outcomes) == 2
    assert {o.asset for o in outcomes} == {"BTC", "ETH"}


def test_build_outcomes_no_assets():
    record = AlertAuditRecord(
        document_id="doc-1",
        channel="telegram",
        message_id="msg-1",
        is_digest=False,
        dispatched_at="2026-01-01T12:00:00+00:00",
        sentiment_label="bullish",
        affected_assets=[],
        priority=8,
        actionable=True,
    )
    outcomes = build_outcomes_from_records([record])
    assert outcomes == []


def test_build_outcomes_blocks_explicitly_ineligible_directional_record():
    record = AlertAuditRecord(
        document_id="doc-1",
        channel="telegram",
        message_id="msg-1",
        is_digest=False,
        dispatched_at="2026-01-01T12:00:00+00:00",
        sentiment_label="bullish",
        affected_assets=["OPENAI"],
        priority=8,
        actionable=True,
        directional_eligible=False,
        directional_block_reason="unsupported_or_non_crypto_assets",
    )
    outcomes = build_outcomes_from_records([record])
    assert outcomes == []


def test_build_outcomes_legacy_non_crypto_asset_fail_closed():
    record = AlertAuditRecord(
        document_id="doc-1",
        channel="telegram",
        message_id="msg-1",
        is_digest=False,
        dispatched_at="2026-01-01T12:00:00+00:00",
        sentiment_label="bullish",
        affected_assets=["DISNEY"],
        priority=8,
        actionable=True,
    )
    outcomes = build_outcomes_from_records([record])
    assert outcomes == []


# ── compute_hit_rate ──────────────────────────────────────────────────


def test_compute_hit_rate_empty():
    report = compute_hit_rate([])
    assert report.total_alerts == 0
    assert report.hit_rate_pct is None
    assert report.sufficient_sample is False


def test_compute_hit_rate_all_resolved():
    outcomes = [
        AlertOutcome(document_id="1", asset="BTC", sentiment_label="bullish",
                     dispatched_at="t", price_at_alert=100.0,
                     price_at_resolution=110.0, is_hit=True, return_pct=10.0),
        AlertOutcome(document_id="2", asset="BTC", sentiment_label="bullish",
                     dispatched_at="t", price_at_alert=100.0,
                     price_at_resolution=90.0, is_hit=False, return_pct=-10.0),
        AlertOutcome(document_id="3", asset="ETH", sentiment_label="bearish",
                     dispatched_at="t", price_at_alert=100.0,
                     price_at_resolution=80.0, is_hit=True, return_pct=-20.0),
    ]
    report = compute_hit_rate(outcomes, min_sample=3)
    assert report.total_alerts == 3
    assert report.resolved_count == 3
    assert report.hit_count == 2
    assert report.miss_count == 1
    assert report.hit_rate_pct == 66.67
    assert report.sufficient_sample is True


def test_compute_hit_rate_mixed_resolved_unresolved():
    outcomes = [
        AlertOutcome(document_id="1", asset="BTC", sentiment_label="bullish",
                     dispatched_at="t", is_hit=True, price_at_alert=100.0,
                     price_at_resolution=110.0, return_pct=10.0),
        AlertOutcome(document_id="2", asset="BTC", sentiment_label="bullish",
                     dispatched_at="t"),  # unresolved
    ]
    report = compute_hit_rate(outcomes, min_sample=1)
    assert report.resolved_count == 1
    assert report.unresolved_count == 1
    assert report.hit_rate_pct == 100.0


def test_compute_hit_rate_sample_uses_resolved_count_not_directional():
    outcomes = [
        AlertOutcome(
            document_id=str(i),
            asset="BTC",
            sentiment_label="bullish",
            dispatched_at="t",
        )
        for i in range(60)
    ]
    report = compute_hit_rate(outcomes, min_sample=50)
    assert report.directional_alerts == 60
    assert report.resolved_count == 0
    assert report.sufficient_sample is False


def test_compute_hit_rate_by_sentiment():
    outcomes = [
        AlertOutcome(document_id="1", asset="BTC", sentiment_label="bullish",
                     dispatched_at="t", is_hit=True, price_at_alert=1.0,
                     price_at_resolution=2.0, return_pct=100.0),
        AlertOutcome(document_id="2", asset="BTC", sentiment_label="bearish",
                     dispatched_at="t", is_hit=False, price_at_alert=1.0,
                     price_at_resolution=2.0, return_pct=100.0),
    ]
    report = compute_hit_rate(outcomes, min_sample=1)
    assert report.by_sentiment["bullish"].hits == 1
    assert report.by_sentiment["bearish"].hits == 0


def test_compute_hit_rate_by_asset():
    outcomes = [
        AlertOutcome(document_id="1", asset="BTC", sentiment_label="bullish",
                     dispatched_at="t", is_hit=True, price_at_alert=1.0,
                     price_at_resolution=2.0, return_pct=100.0),
        AlertOutcome(document_id="2", asset="ETH", sentiment_label="bullish",
                     dispatched_at="t", is_hit=False, price_at_alert=1.0,
                     price_at_resolution=0.5, return_pct=-50.0),
    ]
    report = compute_hit_rate(outcomes, min_sample=1)
    assert "BTC" in report.by_asset
    assert "ETH" in report.by_asset
    assert report.by_asset["BTC"].hit_rate_pct == 100.0
    assert report.by_asset["ETH"].hit_rate_pct == 0.0


def test_compute_hit_rate_insufficient_sample():
    outcomes = [
        AlertOutcome(document_id="1", asset="BTC", sentiment_label="bullish",
                     dispatched_at="t", is_hit=True, price_at_alert=1.0,
                     price_at_resolution=2.0, return_pct=100.0),
    ]
    report = compute_hit_rate(outcomes, min_sample=50)
    assert report.sufficient_sample is False
    assert report.min_sample == 50


# ── AlertAuditRecord enrichment round-trip ────────────────────────────


def test_audit_record_enriched_serialization(tmp_path: Path):
    record = _make_record()
    p = tmp_path / "audit.jsonl"
    append_alert_audit(record, p)

    raw = json.loads(p.read_text(encoding="utf-8").strip())
    assert raw["sentiment_label"] == "bullish"
    assert raw["affected_assets"] == ["BTC"]
    assert raw["priority"] == 8
    assert raw["actionable"] is True


def test_audit_record_serializes_directional_guard_fields(tmp_path: Path):
    record = AlertAuditRecord(
        document_id="doc-guard",
        channel="telegram",
        message_id="msg-guard",
        is_digest=False,
        dispatched_at="2026-01-01T12:00:00+00:00",
        sentiment_label="bearish",
        affected_assets=[],
        priority=7,
        actionable=False,
        directional_eligible=False,
        directional_block_reason="unsupported_or_non_crypto_assets",
        directional_blocked_assets=["OPENAI", "DISNEY"],
    )
    p = tmp_path / "audit.jsonl"
    append_alert_audit(record, p)
    raw = json.loads(p.read_text(encoding="utf-8").strip())
    assert raw["directional_eligible"] is False
    assert raw["directional_block_reason"] == "unsupported_or_non_crypto_assets"
    assert raw["directional_blocked_assets"] == ["OPENAI", "DISNEY"]


def test_audit_record_backward_compatible_load(tmp_path: Path):
    """Old-format records (no prediction fields) load with defaults."""
    p = tmp_path / "audit.jsonl"
    old_record = {
        "document_id": "doc-old",
        "channel": "email",
        "message_id": None,
        "is_digest": False,
        "dispatched_at": "2025-01-01T00:00:00+00:00",
    }
    p.write_text(json.dumps(old_record) + "\n", encoding="utf-8")
    records = load_alert_audits(p)
    assert len(records) == 1
    assert records[0].sentiment_label is None
    assert records[0].affected_assets == []
    assert records[0].priority is None
    assert records[0].actionable is None
    assert records[0].directional_eligible is None
    assert records[0].directional_block_reason is None
    assert records[0].directional_blocked_assets == []


def test_hit_rate_report_to_dict():
    report = compute_hit_rate([], min_sample=50)
    d = report.to_dict()
    assert d["total_alerts"] == 0
    assert d["sufficient_sample"] is False
    assert d["min_sample"] == 50


# ── AHR-1: AlertOutcomeAnnotation ─────────────────────────────────────


def test_outcome_annotation_serialization():
    ann = AlertOutcomeAnnotation(
        document_id="doc-1",
        outcome="hit",
        asset="BTC",
        note="price moved up",
    )
    d = ann.to_json_dict()
    assert d["document_id"] == "doc-1"
    assert d["outcome"] == "hit"
    assert d["asset"] == "BTC"
    assert d["note"] == "price moved up"
    assert "annotated_at" in d


def test_outcome_annotation_optional_fields_omitted():
    ann = AlertOutcomeAnnotation(document_id="doc-2", outcome="miss")
    d = ann.to_json_dict()
    assert "asset" not in d
    assert "note" not in d


def test_append_and_load_outcome_annotations(tmp_path: Path):
    p = tmp_path / "outcomes.jsonl"
    ann1 = AlertOutcomeAnnotation(document_id="doc-1", outcome="hit", asset="BTC")
    ann2 = AlertOutcomeAnnotation(document_id="doc-2", outcome="miss")
    ann3 = AlertOutcomeAnnotation(document_id="doc-3", outcome="inconclusive", note="unclear")
    append_outcome_annotation(ann1, p)
    append_outcome_annotation(ann2, p)
    append_outcome_annotation(ann3, p)

    loaded = load_outcome_annotations(p)
    assert len(loaded) == 3
    assert loaded[0].document_id == "doc-1"
    assert loaded[0].outcome == "hit"
    assert loaded[0].asset == "BTC"
    assert loaded[1].outcome == "miss"
    assert loaded[2].outcome == "inconclusive"
    assert loaded[2].note == "unclear"


def test_load_outcome_annotations_missing_file(tmp_path: Path):
    result = load_outcome_annotations(tmp_path / "nonexistent.jsonl")
    assert result == []


def test_load_outcome_annotations_via_directory(tmp_path: Path):
    ann = AlertOutcomeAnnotation(document_id="doc-1", outcome="hit")
    append_outcome_annotation(ann, tmp_path)  # dir → writes alert_outcomes.jsonl
    loaded = load_outcome_annotations(tmp_path)
    assert len(loaded) == 1


# ── AHR-1: build_outcomes_from_records with annotations ───────────────


def test_build_outcomes_annotation_hit():
    records = [_make_record(doc_id="doc-1", assets=["BTC"])]
    annotations = [AlertOutcomeAnnotation(document_id="doc-1", outcome="hit")]
    outcomes = build_outcomes_from_records(records, annotations=annotations)
    assert len(outcomes) == 1
    assert outcomes[0].is_hit is True
    assert outcomes[0].price_at_alert is None  # no price data


def test_build_outcomes_annotation_miss():
    records = [_make_record(doc_id="doc-1", assets=["ETH"])]
    annotations = [AlertOutcomeAnnotation(document_id="doc-1", outcome="miss")]
    outcomes = build_outcomes_from_records(records, annotations=annotations)
    assert outcomes[0].is_hit is False


def test_build_outcomes_annotation_inconclusive_remains_unresolved():
    records = [_make_record(doc_id="doc-1", assets=["BTC"])]
    annotations = [AlertOutcomeAnnotation(document_id="doc-1", outcome="inconclusive")]
    outcomes = build_outcomes_from_records(records, annotations=annotations)
    assert outcomes[0].is_hit is None


def test_build_outcomes_price_data_takes_precedence_over_annotation():
    """When price data is available, use it; ignore annotation for same record."""
    records = [_make_record(doc_id="doc-1", assets=["BTC"])]
    lookup = {("BTC", "2026-01-01T12:00:00+00:00"): (40000.0, 42000.0, "2026-01-02T12:00:00+00:00")}
    # Annotation says "miss" but price says hit
    annotations = [AlertOutcomeAnnotation(document_id="doc-1", outcome="miss")]
    outcomes = build_outcomes_from_records(records, price_lookup=lookup, annotations=annotations)
    assert outcomes[0].is_hit is True  # price data wins


def test_build_outcomes_annotation_no_match_stays_unresolved():
    records = [_make_record(doc_id="doc-1", assets=["BTC"])]
    annotations = [AlertOutcomeAnnotation(document_id="doc-99", outcome="hit")]  # different doc
    outcomes = build_outcomes_from_records(records, annotations=annotations)
    assert outcomes[0].is_hit is None


def test_build_outcomes_compute_hit_rate_from_annotations():
    """End-to-end: annotations feed into compute_hit_rate."""
    records = [
        _make_record(doc_id="doc-1", assets=["BTC"]),
        _make_record(doc_id="doc-2", assets=["ETH"], sentiment="bearish"),
        _make_record(doc_id="doc-3", assets=["BTC"]),
    ]
    annotations = [
        AlertOutcomeAnnotation(document_id="doc-1", outcome="hit"),
        AlertOutcomeAnnotation(document_id="doc-2", outcome="hit"),
        AlertOutcomeAnnotation(document_id="doc-3", outcome="miss"),
    ]
    outcomes = build_outcomes_from_records(records, annotations=annotations)
    report = compute_hit_rate(outcomes, min_sample=3)
    assert report.resolved_count == 3
    assert report.hit_count == 2
    assert report.miss_count == 1
    assert report.hit_rate_pct == pytest.approx(66.67)
