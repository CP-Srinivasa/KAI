from __future__ import annotations

from app.alerts.audit import AlertAuditRecord, AlertOutcomeAnnotation
from app.alerts.feature_analysis import build_feature_analysis


def _audit(
    doc_id: str,
    sentiment: str,
    assets: list[str],
    priority: int,
    *,
    dispatched_at: str = "2026-03-25T10:00:00+00:00",
    actionable: bool = True,
    normalized_title: str | None = None,
) -> AlertAuditRecord:
    return AlertAuditRecord(
        document_id=doc_id,
        channel="telegram",
        message_id="dry",
        is_digest=False,
        dispatched_at=dispatched_at,
        sentiment_label=sentiment,
        affected_assets=assets,
        priority=priority,
        actionable=actionable,
        directional_eligible=True,
        normalized_title=normalized_title,
    )


def _ann(doc_id: str, outcome: str) -> AlertOutcomeAnnotation:
    return AlertOutcomeAnnotation(
        document_id=doc_id,
        outcome=outcome,  # type: ignore[arg-type]
        annotated_at="2026-03-25T12:00:00+00:00",
    )


def test_totals_reflect_hit_miss_inconclusive_unlabeled() -> None:
    audits = [
        _audit("d1", "bullish", ["BTC"], 8),
        _audit("d2", "bullish", ["BTC"], 8),
        _audit("d3", "bearish", ["ETH"], 6),
        _audit("d4", "bullish", ["BTC"], 5),  # inconclusive
        _audit("d5", "bullish", ["SOL"], 5),  # unlabeled
    ]
    annotations = [
        _ann("d1", "hit"),
        _ann("d2", "miss"),
        _ann("d3", "hit"),
        _ann("d4", "inconclusive"),
    ]

    report = build_feature_analysis(audits, annotations, min_bucket_size=1)

    totals = report["totals"]
    assert totals["directional_alerts"] == 5
    assert totals["hits"] == 2
    assert totals["miss"] == 1
    assert totals["resolved"] == 3
    assert totals["inconclusive"] == 1
    assert totals["unlabeled"] == 1
    assert totals["precision_pct"] == round(2 / 3 * 100, 2)


def test_sentiment_bucket_split() -> None:
    audits = [
        _audit("d1", "bullish", ["BTC"], 8),
        _audit("d2", "bullish", ["BTC"], 8),
        _audit("d3", "bearish", ["ETH"], 6),
        _audit("d4", "bearish", ["ETH"], 6),
    ]
    annotations = [
        _ann("d1", "hit"),
        _ann("d2", "miss"),
        _ann("d3", "miss"),
        _ann("d4", "miss"),
    ]

    report = build_feature_analysis(audits, annotations, min_bucket_size=1)
    by_sent = {b["label"]: b for b in report["buckets"]["by_sentiment"]}

    assert by_sent["bullish"]["resolved"] == 2
    assert by_sent["bullish"]["hits"] == 1
    assert by_sent["bullish"]["precision_pct"] == 50.0
    assert by_sent["bearish"]["resolved"] == 2
    assert by_sent["bearish"]["hits"] == 0
    assert by_sent["bearish"]["precision_pct"] == 0.0


def test_asset_bucket_counts_multi_asset_alerts_in_each_bucket() -> None:
    audits = [
        _audit("d1", "bullish", ["BTC", "ETH"], 8),
        _audit("d2", "bullish", ["BTC"], 8),
        _audit("d3", "bearish", ["ETH"], 6),
    ]
    annotations = [
        _ann("d1", "hit"),
        _ann("d2", "miss"),
        _ann("d3", "hit"),
    ]

    report = build_feature_analysis(audits, annotations, min_bucket_size=1)
    by_asset = {b["label"]: b for b in report["buckets"]["by_asset"]}

    assert by_asset["BTC"]["resolved"] == 2
    assert by_asset["BTC"]["hits"] == 1  # d1 hit, d2 miss
    assert by_asset["BTC"]["precision_pct"] == 50.0
    assert by_asset["ETH"]["resolved"] == 2
    assert by_asset["ETH"]["hits"] == 2  # d1 hit, d3 hit
    assert by_asset["ETH"]["precision_pct"] == 100.0


def test_priority_group_high_vs_low() -> None:
    audits = [
        _audit("d1", "bullish", ["BTC"], 8),  # high
        _audit("d2", "bullish", ["BTC"], 9),  # high
        _audit("d3", "bullish", ["BTC"], 5),  # low
        _audit("d4", "bullish", ["BTC"], 5),  # low
    ]
    annotations = [
        _ann("d1", "hit"),
        _ann("d2", "hit"),
        _ann("d3", "miss"),
        _ann("d4", "miss"),
    ]

    report = build_feature_analysis(audits, annotations, min_bucket_size=1)
    groups = {b["label"]: b for b in report["buckets"]["by_priority_group"]}

    assert groups["high (>=7)"]["precision_pct"] == 100.0
    assert groups["low (<7)"]["precision_pct"] == 0.0


def test_min_bucket_size_filters_noise() -> None:
    audits = [
        _audit("d1", "bullish", ["BTC"], 8),
        _audit("d2", "bullish", ["BTC"], 8),
        _audit("d3", "bullish", ["BTC"], 8),
        _audit("d4", "bullish", ["SOL"], 8),  # single obs for SOL
    ]
    annotations = [
        _ann("d1", "hit"),
        _ann("d2", "miss"),
        _ann("d3", "hit"),
        _ann("d4", "miss"),
    ]

    report = build_feature_analysis(audits, annotations, min_bucket_size=3)
    labels = {b["label"] for b in report["buckets"]["by_asset"]}
    assert "BTC" in labels
    assert "SOL" not in labels  # below min_bucket_size


def test_latest_dispatch_per_document_wins() -> None:
    audits = [
        _audit(
            "d1", "bullish", ["BTC"], 5,
            dispatched_at="2026-03-25T10:00:00+00:00",
        ),
        _audit(
            "d1", "bullish", ["BTC"], 9,  # later dispatch with different priority
            dispatched_at="2026-03-25T11:00:00+00:00",
        ),
    ]
    annotations = [_ann("d1", "hit")]

    report = build_feature_analysis(audits, annotations, min_bucket_size=1)
    labels = [b["label"] for b in report["buckets"]["by_priority"]]
    assert labels == ["p9"]


def test_source_bucket_optional_and_present_when_provided() -> None:
    audits = [
        _audit("d1", "bullish", ["BTC"], 8),
        _audit("d2", "bullish", ["BTC"], 8),
        _audit("d3", "bullish", ["BTC"], 8),
    ]
    annotations = [
        _ann("d1", "hit"),
        _ann("d2", "miss"),
        _ann("d3", "hit"),
    ]

    # Without source lookup → bucket must be absent
    report_no_src = build_feature_analysis(audits, annotations, min_bucket_size=1)
    assert "by_source" not in report_no_src["buckets"]

    # With source lookup → bucket present and aggregated
    report_src = build_feature_analysis(
        audits,
        annotations,
        source_by_doc={"d1": "CoinDesk", "d2": "CoinDesk", "d3": "Decrypt"},
        min_bucket_size=1,
    )
    by_src = {b["label"]: b for b in report_src["buckets"]["by_source"]}
    assert by_src["CoinDesk"]["resolved"] == 2
    assert by_src["CoinDesk"]["precision_pct"] == 50.0
    assert by_src["Decrypt"]["resolved"] == 1
    assert by_src["Decrypt"]["precision_pct"] == 100.0


# ── D-134: Forward precision simulation ──────────────────────────────────


def test_forward_simulation_filters_bearish() -> None:
    """D-142 bearish block reduces resolved count in forward sim."""
    audits = [
        _audit("d1", "bullish", ["BTC"], 8),
        _audit("d2", "bearish", ["BTC"], 8),
        _audit("d3", "bullish", ["ETH"], 9),
    ]
    annotations = [
        _ann("d1", "hit"),
        _ann("d2", "miss"),
        _ann("d3", "hit"),
    ]
    report = build_feature_analysis(audits, annotations, min_bucket_size=1)
    fwd = report["forward_simulation"]
    # d2 bearish filtered out → 2 resolved, 2 hits
    assert fwd["resolved"] == 2
    assert fwd["hits"] == 2
    assert fwd["miss"] == 0
    assert fwd["filtered_out"] == 1
    assert fwd["precision_pct"] == 100.0


def test_forward_simulation_filters_low_priority() -> None:
    """D-123 priority ≤7 block reduces resolved count in forward sim."""
    audits = [
        _audit("d1", "bullish", ["BTC"], 9),
        _audit("d2", "bullish", ["BTC"], 7),  # blocked
        _audit("d3", "bullish", ["BTC"], 5),  # blocked
    ]
    annotations = [
        _ann("d1", "hit"),
        _ann("d2", "miss"),
        _ann("d3", "miss"),
    ]
    report = build_feature_analysis(audits, annotations, min_bucket_size=1)
    fwd = report["forward_simulation"]
    assert fwd["resolved"] == 1
    assert fwd["hits"] == 1
    assert fwd["filtered_out"] == 2
    assert fwd["precision_pct"] == 100.0


def test_forward_simulation_filters_low_precision_source() -> None:
    """D-133 source block reduces resolved count in forward sim."""
    audits = [
        _audit("d1", "bullish", ["BTC"], 8),
        _audit("d2", "bullish", ["BTC"], 8),
        _audit("d3", "bullish", ["BTC"], 8),
    ]
    annotations = [
        _ann("d1", "hit"),
        _ann("d2", "miss"),
        _ann("d3", "hit"),
    ]
    source_by_doc = {"d1": "coindesk", "d2": "decrypt", "d3": "cointelegraph"}
    report = build_feature_analysis(
        audits, annotations, source_by_doc=source_by_doc, min_bucket_size=1,
    )
    fwd = report["forward_simulation"]
    # d2 from decrypt filtered out
    assert fwd["resolved"] == 2
    assert fwd["hits"] == 2
    assert fwd["miss"] == 0
    assert fwd["filtered_out"] == 1


def test_forward_simulation_filters_not_actionable() -> None:
    """D-122 actionable=false block in forward sim."""
    audits = [
        _audit("d1", "bullish", ["BTC"], 8, actionable=True),
        _audit("d2", "bullish", ["BTC"], 8, actionable=False),
    ]
    annotations = [
        _ann("d1", "hit"),
        _ann("d2", "miss"),
    ]
    report = build_feature_analysis(audits, annotations, min_bucket_size=1)
    fwd = report["forward_simulation"]
    assert fwd["resolved"] == 1
    assert fwd["hits"] == 1
    assert fwd["filtered_out"] == 1


def test_forward_simulation_combined_filters() -> None:
    """Multiple gates combine to filter in forward sim."""
    audits = [
        _audit("d1", "bullish", ["BTC"], 9),       # passes all
        _audit("d2", "bearish", ["BTC"], 9),        # bearish block
        _audit("d3", "bullish", ["BTC"], 7),        # priority block
        _audit("d4", "bullish", ["BTC"], 8),        # source block
        _audit("d5", "bullish", ["ETH"], 8),        # passes
    ]
    annotations = [
        _ann("d1", "hit"),
        _ann("d2", "miss"),
        _ann("d3", "miss"),
        _ann("d4", "miss"),
        _ann("d5", "hit"),
    ]
    source_by_doc = {
        "d1": "coindesk", "d2": "coindesk", "d3": "coindesk",
        "d4": "bitcoin_magazine", "d5": "beincrypto",
    }
    report = build_feature_analysis(
        audits, annotations, source_by_doc=source_by_doc, min_bucket_size=1,
    )
    fwd = report["forward_simulation"]
    # d1 + d5 pass, d2/d3/d4 filtered
    assert fwd["resolved"] == 2
    assert fwd["hits"] == 2
    assert fwd["miss"] == 0
    assert fwd["filtered_out"] == 3
    assert fwd["precision_pct"] == 100.0


def test_forward_simulation_filters_reactive_bullish_title() -> None:
    """Bullish reactive narrative in title blocks in forward sim."""
    audits = [
        _audit("d1", "bullish", ["BTC"], 9),
        _audit(
            "d2", "bullish", ["BTC"], 10,
            normalized_title="btc etf empire surging past $100b",
        ),
        _audit("d3", "bullish", ["ETH"], 9),
    ]
    annotations = [
        _ann("d1", "hit"),
        _ann("d2", "miss"),
        _ann("d3", "hit"),
    ]
    report = build_feature_analysis(audits, annotations, min_bucket_size=1)
    fwd = report["forward_simulation"]
    # d2 "surging" → reactive bullish → filtered
    assert fwd["resolved"] == 2
    assert fwd["hits"] == 2
    assert fwd["miss"] == 0
    assert fwd["filtered_out"] == 1


def test_forward_simulation_uses_title_by_doc_fallback() -> None:
    """title_by_doc provides title for old records without normalized_title."""
    audits = [
        _audit("d1", "bullish", ["BTC"], 9),       # no title
        _audit("d2", "bullish", ["BTC"], 10),       # no title
    ]
    annotations = [
        _ann("d1", "hit"),
        _ann("d2", "miss"),
    ]
    # Without title_by_doc: both pass (no reactive check)
    report_no = build_feature_analysis(audits, annotations, min_bucket_size=1)
    assert report_no["forward_simulation"]["resolved"] == 2

    # With title_by_doc: d2 gets reactive title from DB → filtered
    title_by_doc = {"d2": "bitcoin price eyes breakout as oil drops"}
    report_with = build_feature_analysis(
        audits, annotations, title_by_doc=title_by_doc, min_bucket_size=1,
    )
    fwd = report_with["forward_simulation"]
    assert fwd["resolved"] == 1
    assert fwd["hits"] == 1
    assert fwd["miss"] == 0
    assert fwd["filtered_out"] == 1


def test_excludes_digests_and_non_directional_sentiments() -> None:
    audits = [
        AlertAuditRecord(
            document_id="digest-1",
            channel="telegram",
            message_id="d",
            is_digest=True,
            sentiment_label="bullish",
            affected_assets=["BTC"],
            priority=8,
            directional_eligible=True,
        ),
        _audit("d-mixed", "mixed", ["BTC"], 8),  # non-directional
        _audit("d-real", "bullish", ["BTC"], 8),
    ]
    annotations = [_ann("d-real", "hit"), _ann("digest-1", "hit")]

    report = build_feature_analysis(audits, annotations, min_bucket_size=1)
    assert report["totals"]["directional_alerts"] == 1
    assert report["totals"]["hits"] == 1
