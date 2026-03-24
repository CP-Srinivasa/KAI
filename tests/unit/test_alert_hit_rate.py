"""Tests for alert hit-rate computation."""

from __future__ import annotations

import json
from pathlib import Path

from app.alerts.audit import AlertAuditRecord, append_alert_audit, load_alert_audits
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
    assert len(outcomes) == 1
    assert outcomes[0].asset == "unknown"


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


def test_hit_rate_report_to_dict():
    report = compute_hit_rate([], min_sample=50)
    d = report.to_dict()
    assert d["total_alerts"] == 0
    assert d["sufficient_sample"] is False
    assert d["min_sample"] == 50
