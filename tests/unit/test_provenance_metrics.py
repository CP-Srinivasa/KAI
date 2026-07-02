"""Tests for TV-4 provenance-split quality-bar metrics."""

from __future__ import annotations

import json
from pathlib import Path

from app.alerts.provenance_metrics import (
    MIN_SAMPLE_FOR_JUDGMENT,
    build_provenance_split_report,
    wilson_ci,
    write_provenance_report,
)


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(json.dumps(row) for row in rows)
    path.write_text(text + ("\n" if text else ""), encoding="utf-8")


# --- Wilson CI ---


def test_wilson_ci_returns_none_for_empty_sample() -> None:
    assert wilson_ci(0, 0) is None


def test_wilson_ci_symmetric_at_half() -> None:
    low, high = wilson_ci(50, 100)
    assert 0.0 < low < 0.5 < high < 1.0
    assert abs((0.5 - low) - (high - 0.5)) < 0.001  # symmetry


def test_wilson_ci_narrower_with_larger_sample() -> None:
    _, high_small = wilson_ci(5, 10)
    _, high_large = wilson_ci(500, 1000)
    low_small, _ = wilson_ci(5, 10)
    low_large, _ = wilson_ci(500, 1000)
    assert (high_small - low_small) > (high_large - low_large)


def test_wilson_ci_bounded_zero_to_one() -> None:
    low, high = wilson_ci(0, 10)
    assert low == 0.0
    assert 0.0 < high < 1.0
    low, high = wilson_ci(10, 10)
    assert 0.0 < low < 1.0
    assert high == 1.0


# --- Split Report ---


def _setup_paths(tmp_path: Path) -> tuple[Path, Path, Path]:
    return (
        tmp_path / "alert_audit.jsonl",
        tmp_path / "alert_outcomes.jsonl",
        tmp_path / "tradingview_pending_signals.jsonl",
    )


def test_split_report_groups_by_source(tmp_path: Path) -> None:
    alerts, outcomes, tv = _setup_paths(tmp_path)
    _write_jsonl(
        alerts,
        [
            {
                "document_id": f"doc-rss-{i}",
                "channel": "telegram",
                "message_id": "dry_run",
                "is_digest": False,
                "dispatched_at": "2026-04-01T10:00:00+00:00",
                "sentiment_label": "bullish",
                "affected_assets": ["BTC"],
            }
            for i in range(6)
        ],
    )
    _write_jsonl(
        outcomes,
        [
            {"document_id": "doc-rss-0", "outcome": "hit"},
            {"document_id": "doc-rss-1", "outcome": "hit"},
            {"document_id": "doc-rss-2", "outcome": "miss"},
            {"document_id": "doc-rss-3", "outcome": "miss"},
            {"document_id": "doc-rss-4", "outcome": "miss"},
            {"document_id": "doc-rss-5", "outcome": "inconclusive"},
        ],
    )
    _write_jsonl(tv, [])

    source_map = {f"doc-rss-{i}": "rss" for i in range(6)}

    report = build_provenance_split_report(
        alert_audit_path=alerts,
        alert_outcomes_path=outcomes,
        tradingview_pending_signals_path=tv,
        source_by_doc=source_map,
    )

    assert report.overall.resolved == 5  # inconclusive excluded
    assert report.overall.hits == 2
    assert report.overall.misses == 3
    assert report.overall.hit_rate_pct == 40.0

    assert len(report.by_source) == 1
    rss = report.by_source[0]
    assert rss.source == "rss"
    assert rss.hits == 2
    assert rss.misses == 3
    assert rss.hit_rate_pct == 40.0
    assert rss.ci_low_pct is not None
    assert rss.ci_high_pct is not None
    assert rss.ci_low_pct < 40.0 < rss.ci_high_pct


def test_split_report_flags_insufficient_sample(tmp_path: Path) -> None:
    alerts, outcomes, tv = _setup_paths(tmp_path)
    _write_jsonl(
        alerts,
        [
            {
                "document_id": "doc-1",
                "channel": "telegram",
                "message_id": "dry_run",
                "is_digest": False,
                "dispatched_at": "2026-04-01T10:00:00+00:00",
                "sentiment_label": "bullish",
                "affected_assets": ["BTC"],
            }
        ],
    )
    _write_jsonl(
        outcomes,
        [{"document_id": "doc-1", "outcome": "hit"}],
    )
    _write_jsonl(tv, [])

    report = build_provenance_split_report(
        alert_audit_path=alerts,
        alert_outcomes_path=outcomes,
        tradingview_pending_signals_path=tv,
        source_by_doc={"doc-1": "rss"},
    )
    assert report.overall.sample_sufficient is False
    assert report.by_source[0].sample_sufficient is False
    assert MIN_SAMPLE_FOR_JUDGMENT >= 30


def test_split_report_unknown_source_fallback(tmp_path: Path) -> None:
    alerts, outcomes, tv = _setup_paths(tmp_path)
    _write_jsonl(
        alerts,
        [
            {
                "document_id": "doc-unmapped",
                "channel": "telegram",
                "message_id": "dry_run",
                "is_digest": False,
                "dispatched_at": "2026-04-01T10:00:00+00:00",
                "sentiment_label": "bullish",
                "affected_assets": ["BTC"],
            }
        ],
    )
    _write_jsonl(outcomes, [{"document_id": "doc-unmapped", "outcome": "hit"}])
    _write_jsonl(tv, [])

    report = build_provenance_split_report(
        alert_audit_path=alerts,
        alert_outcomes_path=outcomes,
        tradingview_pending_signals_path=tv,
        source_by_doc=None,  # no mapping at all
    )
    assert len(report.by_source) == 1
    assert report.by_source[0].source == "unknown"


def test_tv_pipeline_summary_counts_smoke_and_real(tmp_path: Path) -> None:
    alerts, outcomes, tv = _setup_paths(tmp_path)
    _write_jsonl(alerts, [])
    _write_jsonl(outcomes, [])
    _write_jsonl(
        tv,
        [
            {
                "event_id": "tvsig_a",
                "note": "smoke-test tv-3 happy",
                "provenance": {"signal_path_id": "tvpath_1"},
            },
            {
                "event_id": "tvsig_b",
                "note": "Real TV alert",
                "provenance": {"signal_path_id": "tvpath_2"},
            },
            {
                "event_id": "tvsig_c",
                "note": "another real alert",
                "provenance": {"signal_path_id": "tvpath_2"},  # same path
            },
        ],
    )

    report = build_provenance_split_report(
        alert_audit_path=alerts,
        alert_outcomes_path=outcomes,
        tradingview_pending_signals_path=tv,
    )
    assert report.tradingview_pipeline.pending_events == 3
    assert report.tradingview_pipeline.smoke_test_events == 1
    assert report.tradingview_pipeline.real_events == 2
    assert report.tradingview_pipeline.unique_signal_path_ids == 2


def test_verdict_insufficient_when_tv_empty(tmp_path: Path) -> None:
    alerts, outcomes, tv = _setup_paths(tmp_path)
    _write_jsonl(
        alerts,
        [
            {
                "document_id": f"doc-{i}",
                "channel": "telegram",
                "message_id": "dry_run",
                "is_digest": False,
                "dispatched_at": "2026-04-01T10:00:00+00:00",
                "sentiment_label": "bullish",
                "affected_assets": ["BTC"],
            }
            for i in range(50)
        ],
    )
    _write_jsonl(
        outcomes,
        [{"document_id": f"doc-{i}", "outcome": "hit" if i < 20 else "miss"} for i in range(50)],
    )
    _write_jsonl(tv, [])

    report = build_provenance_split_report(
        alert_audit_path=alerts,
        alert_outcomes_path=outcomes,
        tradingview_pending_signals_path=tv,
        source_by_doc={f"doc-{i}": "rss" for i in range(50)},
    )
    assert report.verdict == "insufficient_sample_for_split_comparison"
    assert any("tradingview_webhook" in n for n in report.notes)


def test_write_provenance_report_roundtrip(tmp_path: Path) -> None:
    alerts, outcomes, tv = _setup_paths(tmp_path)
    _write_jsonl(alerts, [])
    _write_jsonl(outcomes, [])
    _write_jsonl(tv, [])
    report = build_provenance_split_report(
        alert_audit_path=alerts,
        alert_outcomes_path=outcomes,
        tradingview_pending_signals_path=tv,
    )
    out_path = tmp_path / "tv4" / "report.json"
    written = write_provenance_report(report, out_path)
    assert written == out_path
    payload = json.loads(written.read_text(encoding="utf-8"))
    assert payload["report_type"] == "tv4_quality_bar_provenance_split"
    assert payload["verdict"] == report.verdict
    assert "overall" in payload
    assert "overall_active" in payload
    assert "by_source" in payload
    assert "tradingview_pipeline" in payload


def test_overall_active_excludes_unknown_bucket(tmp_path: Path) -> None:
    """V3: overall_active is the same metric without unknown legacy bucket."""
    alerts, outcomes, tv = _setup_paths(tmp_path)
    # 4 RSS-tagged docs (3 hit, 1 miss) + 6 unknown-tagged (1 hit, 5 miss).
    # Baseline: 4 hits / 10 resolved = 40%.
    # Active (without unknown): 3 hits / 4 resolved = 75%.
    docs = [
        {
            "document_id": f"doc-{i}",
            "channel": "x",
            "message_id": "x",
            "is_digest": False,
            "dispatched_at": "2026-04-01T10:00:00+00:00",
            "sentiment_label": "bullish",
            "affected_assets": ["BTC"],
        }
        for i in range(10)
    ]
    _write_jsonl(alerts, docs)
    _write_jsonl(
        outcomes,
        [
            {"document_id": "doc-0", "outcome": "hit"},
            {"document_id": "doc-1", "outcome": "hit"},
            {"document_id": "doc-2", "outcome": "hit"},
            {"document_id": "doc-3", "outcome": "miss"},
            {"document_id": "doc-4", "outcome": "hit"},
            {"document_id": "doc-5", "outcome": "miss"},
            {"document_id": "doc-6", "outcome": "miss"},
            {"document_id": "doc-7", "outcome": "miss"},
            {"document_id": "doc-8", "outcome": "miss"},
            {"document_id": "doc-9", "outcome": "miss"},
        ],
    )
    _write_jsonl(tv, [])
    # Only the first 4 docs are RSS-tagged; the rest have no source -> unknown.
    source_map = {f"doc-{i}": "rss" for i in range(4)}

    report = build_provenance_split_report(
        alert_audit_path=alerts,
        alert_outcomes_path=outcomes,
        tradingview_pending_signals_path=tv,
        source_by_doc=source_map,
    )

    assert report.overall.resolved == 10
    assert report.overall.hits == 4
    assert report.overall.hit_rate_pct == 40.0

    # Active excludes the unknown bucket.
    assert report.overall_active.resolved == 4
    assert report.overall_active.hits == 3
    assert report.overall_active.misses == 1
    assert report.overall_active.hit_rate_pct == 75.0


def test_overall_active_equals_overall_when_no_unknown(tmp_path: Path) -> None:
    """When every doc is source-tagged, active and baseline coincide."""
    alerts, outcomes, tv = _setup_paths(tmp_path)
    docs = [
        {
            "document_id": f"doc-{i}",
            "channel": "x",
            "message_id": "x",
            "is_digest": False,
            "dispatched_at": "2026-04-01T10:00:00+00:00",
            "sentiment_label": "bullish",
            "affected_assets": ["BTC"],
        }
        for i in range(3)
    ]
    _write_jsonl(alerts, docs)
    _write_jsonl(
        outcomes,
        [
            {"document_id": "doc-0", "outcome": "hit"},
            {"document_id": "doc-1", "outcome": "miss"},
            {"document_id": "doc-2", "outcome": "hit"},
        ],
    )
    _write_jsonl(tv, [])
    source_map = {f"doc-{i}": "rss" for i in range(3)}

    report = build_provenance_split_report(
        alert_audit_path=alerts,
        alert_outcomes_path=outcomes,
        tradingview_pending_signals_path=tv,
        source_by_doc=source_map,
    )
    assert report.overall.hit_rate_pct == report.overall_active.hit_rate_pct
    assert report.overall.resolved == report.overall_active.resolved
