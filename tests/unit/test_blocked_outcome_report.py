from __future__ import annotations

import json
from pathlib import Path

from app.alerts.blocked_outcome_report import (
    build_blocked_outcome_report,
    render_blocked_outcome_report,
)


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )


def test_blocked_outcome_report_counts_raw_distinct_latest_and_reeval(tmp_path: Path) -> None:
    _write_jsonl(
        tmp_path / "blocked_outcomes.jsonl",
        [
            {
                "document_id": "d1",
                "outcome": "inconclusive",
                "annotated_at": "2026-06-08T08:00:00+00:00",
                "note": "initial",
                "block_reason": "low_directional_confidence",
                "source_name": "cryptobriefing",
                "sentiment_label": "bullish",
                "directional_confidence": 0.64,
            },
            {
                "document_id": "d1",
                "outcome": "hit",
                "annotated_at": "2026-06-08T12:00:00+00:00",
                "note": "reeval[blocked:low_directional_confidence]",
                "block_reason": "low_directional_confidence",
                "source_name": "cryptobriefing",
                "sentiment_label": "bullish",
                "directional_confidence": 0.64,
            },
            {
                "document_id": "d2",
                "outcome": "miss",
                "annotated_at": "2026-06-08T09:00:00+00:00",
                "block_reason": "bearish_directional_disabled",
                "source_name": "cointelegraph",
                "sentiment_label": "bearish",
                "directional_confidence": 0.95,
            },
        ],
    )

    report = build_blocked_outcome_report(tmp_path)

    assert report["raw_events_count"] == 3
    assert report["distinct_document_id_count"] == 2
    assert report["reevaluation_count"] == 1
    latest = report["latest_outcome_by_document_id"]
    assert latest["d1"]["outcome"] == "hit"
    assert latest["d2"]["outcome"] == "miss"

    by_reason = {row["block_reason"]: row for row in report["hit_miss_by_block_reason"]}
    assert by_reason["low_directional_confidence"]["hit"] == 1
    assert by_reason["bearish_directional_disabled"]["miss"] == 1

    by_source = {row["source"]: row for row in report["hit_miss_by_source"]}
    assert by_source["cryptobriefing"]["hit"] == 1
    assert by_source["cointelegraph"]["miss"] == 1

    by_sentiment = {row["sentiment"]: row for row in report["hit_miss_by_sentiment"]}
    assert by_sentiment["bullish"]["hit"] == 1
    assert by_sentiment["bearish"]["miss"] == 1

    by_conf = {row["confidence_bucket"]: row for row in report["hit_miss_by_confidence"]}
    assert by_conf["0.6-0.7"]["hit"] == 1
    assert by_conf["0.9-1.0"]["miss"] == 1
    assert "raw_events_count: 3" in render_blocked_outcome_report(report)
