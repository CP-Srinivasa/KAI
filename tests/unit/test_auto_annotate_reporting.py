"""Unit tests for the Auto-Annotate Cohort Reporting split.

Verifies the 8 test scenarios defined in docs/architecture/auto_annotate_reporting_split_spec.md.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

from app.alerts.audit import (
    AlertOutcomeAnnotation,
    AlertAuditRecord,
    append_outcome_annotation,
    append_alert_audit,
)
from app.alerts.reporting import generate_cohort_report


def create_mock_outcome(
    doc_id: str,
    outcome: str,
    note: str,
    annot_time: str,
) -> AlertOutcomeAnnotation:
    return AlertOutcomeAnnotation(
        document_id=doc_id,
        outcome=outcome,  # type: ignore
        annotated_at=annot_time,
        asset="BTC/USDT",
        note=note,
    )


def test_cohort_split_routing_and_rates(tmp_path: Path) -> None:
    # 1. Fresh auto rows counted only in fresh_auto
    # 2. Backfill rows counted only in backfill
    # 3. Reeval rows counted only in reeval
    # 4. Unknown or legacy notes counted under other
    # 6. Inconclusive excluded from resolved hit-rate
    
    t_now = datetime.now(timezone.utc).isoformat()
    
    # Create outcome annotations
    outcomes = [
        # fresh_auto (resolved = 2, hit = 1, miss = 1, inconclusive = 1) -> hit_rate = 50%
        create_mock_outcome("doc_1", "hit", "auto: bullish BTC/USDT", t_now),
        create_mock_outcome("doc_2", "miss", "auto: bearish BTC/USDT", t_now),
        create_mock_outcome("doc_3", "inconclusive", "auto: bullish BTC/USDT", t_now),
        
        # backfill (resolved = 1, hit = 1, miss = 0, inconclusive = 1) -> hit_rate = 100%
        create_mock_outcome("doc_4", "hit", "backfill: bullish BTC/USDT", t_now),
        create_mock_outcome("doc_5", "inconclusive", "backfill: bearish BTC/USDT", t_now),
        
        # reeval (resolved = 1, hit = 0, miss = 1, inconclusive = 0) -> hit_rate = 0%
        create_mock_outcome("doc_6", "miss", "reeval: bearish BTC/USDT", t_now),
        
        # other (resolved = 2, hit = 1, miss = 1, inconclusive = 0) -> hit_rate = 50%
        create_mock_outcome("doc_7", "hit", "legacy notes without prefix", t_now),
        create_mock_outcome("doc_8", "miss", "manual: bullish BTC/USDT", t_now),
    ]

    for o in outcomes:
        append_outcome_annotation(o, tmp_path)

    # Generate the report
    report = generate_cohort_report(tmp_path)

    cohorts = report["cohorts"]

    # 1. Test fresh_auto cohort
    assert cohorts["fresh_auto"]["total"] == 3
    assert cohorts["fresh_auto"]["hit"] == 1
    assert cohorts["fresh_auto"]["miss"] == 1
    assert cohorts["fresh_auto"]["inconclusive"] == 1
    assert cohorts["fresh_auto"]["resolved"] == 2
    assert cohorts["fresh_auto"]["hit_rate_pct"] == 50.0
    assert cohorts["fresh_auto"]["inconclusive_pct"] == (1 / 3) * 100

    # 2. Test backfill cohort
    assert cohorts["backfill"]["total"] == 2
    assert cohorts["backfill"]["hit"] == 1
    assert cohorts["backfill"]["miss"] == 0
    assert cohorts["backfill"]["inconclusive"] == 1
    assert cohorts["backfill"]["resolved"] == 1
    assert cohorts["backfill"]["hit_rate_pct"] == 100.0

    # 3. Test reeval cohort
    assert cohorts["reeval"]["total"] == 1
    assert cohorts["reeval"]["hit"] == 0
    assert cohorts["reeval"]["miss"] == 1
    assert cohorts["reeval"]["inconclusive"] == 0
    assert cohorts["reeval"]["resolved"] == 1
    assert cohorts["reeval"]["hit_rate_pct"] == 0.0

    # 4. Test other cohort
    assert cohorts["other"]["total"] == 2
    assert cohorts["other"]["hit"] == 1
    assert cohorts["other"]["miss"] == 1
    assert cohorts["other"]["resolved"] == 2
    assert cohorts["other"]["hit_rate_pct"] == 50.0


def test_latest_per_doc_deduplication(tmp_path: Path) -> None:
    # 5. latest_per_doc keeps the newest annotated_at
    
    t_old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    t_new = datetime.now(timezone.utc).isoformat()
    
    # Doc 1 has an older inconclusive outcome, and a newer hit outcome
    # Doc 2 has an older hit outcome, and a newer miss outcome
    outcomes = [
        create_mock_outcome("doc_1", "inconclusive", "auto: bullish BTC/USDT", t_old),
        create_mock_outcome("doc_1", "hit", "auto: bullish BTC/USDT", t_new),
        create_mock_outcome("doc_2", "hit", "auto: bearish BTC/USDT", t_old),
        create_mock_outcome("doc_2", "miss", "auto: bearish BTC/USDT", t_new),
    ]

    for o in outcomes:
        append_outcome_annotation(o, tmp_path)

    report = generate_cohort_report(tmp_path)
    latest = report["cohorts"]["latest_per_doc"]

    assert latest["raw_rows"] == 4
    assert latest["unique_document_ids"] == 2
    assert latest["duplicate_rows_removed"] == 2
    assert latest["hit"] == 1
    assert latest["miss"] == 1
    assert latest["inconclusive"] == 0


def test_dispatch_window_filtering_and_missing_audit(tmp_path: Path) -> None:
    # 7. Dispatch-window filtering does not count old alerts merely because they were annotated inside the window
    # 8. Missing audit joins increment missing_audit instead of crashing
    
    since = datetime.now(timezone.utc) - timedelta(days=1)
    until = datetime.now(timezone.utc) + timedelta(days=1)
    
    t_inside = datetime.now(timezone.utc).isoformat()
    t_outside = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    
    # Create Audits
    # Audit 1 is dispatched INSIDE the window
    aud_1 = AlertAuditRecord(
        document_id="doc_1",
        channel="telegram",
        message_id="123",
        is_digest=False,
        dispatched_at=t_inside,
    )
    # Audit 2 is dispatched OUTSIDE the window
    aud_2 = AlertAuditRecord(
        document_id="doc_2",
        channel="telegram",
        message_id="124",
        is_digest=False,
        dispatched_at=t_outside,
    )
    
    append_alert_audit(aud_1, tmp_path)
    append_alert_audit(aud_2, tmp_path)
    
    # Create Outcomes (both annotated inside the window / today)
    out_1 = create_mock_outcome("doc_1", "hit", "auto: bullish BTC/USDT", t_inside)
    out_2 = create_mock_outcome("doc_2", "hit", "auto: bullish BTC/USDT", t_inside)
    out_3 = create_mock_outcome("doc_3", "hit", "auto: bullish BTC/USDT", t_inside)  # missing audit!
    
    append_outcome_annotation(out_1, tmp_path)
    append_outcome_annotation(out_2, tmp_path)
    append_outcome_annotation(out_3, tmp_path)
    
    # Generate report with dispatch window filters
    report = generate_cohort_report(tmp_path, since=since, until=until)
    fd = report["cohorts"]["fresh_dispatch"]
    
    # doc_1: inside -> counted
    # doc_2: outside -> skipped
    # doc_3: missing -> counted as missing_audit
    assert fd["total"] == 1
    assert fd["hit"] == 1
    assert fd["missing_audit"] == 1
