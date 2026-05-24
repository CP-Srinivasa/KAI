"""Auto-Annotate Cohort Reporting (V5 Follow-up).

Provides a reporting split to evaluate different cohorts:
- fresh_auto (note starts with 'auto:')
- backfill (note starts with 'backfill:')
- reeval (note starts with 'reeval:')
- other (everything else, unknown prefix, catchup, legacy)
- latest_per_doc (de-duplicated by document_id)
- fresh_dispatch (joined with alert_audit.jsonl for dispatch-window filtering)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.alerts.audit import (
    AlertOutcomeAnnotation,
    load_alert_audits,
    load_outcome_annotations,
)


def parse_utc_timestamp(ts_str: str | None) -> datetime | None:
    """Parse a datetime string to a timezone-aware UTC datetime."""
    if not ts_str:
        return None
    try:
        # standard ISO format parsing
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def calculate_cohort_counters(annotations: list[AlertOutcomeAnnotation]) -> dict[str, Any]:
    """Calculate standard counters (total, hit, miss, inconclusive, resolved, rates) for a cohort."""
    total = len(annotations)
    hit = sum(1 for a in annotations if a.outcome == "hit")
    miss = sum(1 for a in annotations if a.outcome == "miss")
    inconclusive = sum(1 for a in annotations if a.outcome == "inconclusive")
    resolved = hit + miss

    hit_rate_pct = None
    if resolved > 0:
        hit_rate_pct = (hit / resolved) * 100

    inconclusive_pct = None
    if total > 0:
        inconclusive_pct = (inconclusive / total) * 100

    return {
        "total": total,
        "hit": hit,
        "miss": miss,
        "inconclusive": inconclusive,
        "resolved": resolved,
        "hit_rate_pct": hit_rate_pct,
        "inconclusive_pct": inconclusive_pct,
    }


def generate_cohort_report(
    audit_dir: Path,
    since: datetime | None = None,
    until: datetime | None = None,
    use_dispatched_at: bool = False,
) -> dict[str, Any]:
    """Generate the auto-annotate cohort report from JSONL files in the audit directory."""
    outcomes = load_outcome_annotations(audit_dir)
    audits = []
    try:
        audits = load_alert_audits(audit_dir)
    except Exception:
        # Fallback if alert_audit.jsonl doesn't exist
        pass

    # Index audits by document_id (newest dispatched_at wins)
    audits_by_doc = {}
    for r in audits:
        dt = parse_utc_timestamp(r.dispatched_at)
        if dt is not None:
            # We keep the newest dispatched record for de-duplication/lookups
            if r.document_id not in audits_by_doc:
                audits_by_doc[r.document_id] = (r, dt)
            else:
                _, existing_dt = audits_by_doc[r.document_id]
                if dt > existing_dt:
                    audits_by_doc[r.document_id] = (r, dt)

    # 1. Base filtering & diagnostics
    filtered_outcomes: list[AlertOutcomeAnnotation] = []
    invalid_timestamp = 0

    since_utc = since.astimezone(timezone.utc) if since is not None else None
    until_utc = until.astimezone(timezone.utc) if until is not None else None

    for a in outcomes:
        annot_dt = parse_utc_timestamp(a.annotated_at)
        if annot_dt is None:
            invalid_timestamp += 1
            # If standard date filtering is active, skip rows with invalid timestamp
            if since_utc is not None or until_utc is not None:
                continue

        # Check date window if set
        if since_utc is not None and annot_dt is not None and annot_dt < since_utc:
            continue
        if until_utc is not None and annot_dt is not None and annot_dt > until_utc:
            continue

        filtered_outcomes.append(a)

    # 2. Split into cohorts based on outcome note prefixes
    fresh_auto_list: list[AlertOutcomeAnnotation] = []
    backfill_list: list[AlertOutcomeAnnotation] = []
    reeval_list: list[AlertOutcomeAnnotation] = []
    other_list: list[AlertOutcomeAnnotation] = []

    for a in filtered_outcomes:
        note = (a.note or "").strip()
        if note.startswith("auto:"):
            fresh_auto_list.append(a)
        elif note.startswith("backfill:"):
            backfill_list.append(a)
        elif note.startswith("reeval:"):
            reeval_list.append(a)
        else:
            other_list.append(a)

    # 3. Latest per doc (keyed by document_id, newest annotated_at wins)
    # This de-duplicates over the *filtered* outcomes for the current window.
    latest_by_doc_map: dict[str, tuple[AlertOutcomeAnnotation, datetime]] = {}
    for a in filtered_outcomes:
        annot_dt = parse_utc_timestamp(a.annotated_at) or datetime.fromtimestamp(0, tz=timezone.utc)
        if a.document_id not in latest_by_doc_map:
            latest_by_doc_map[a.document_id] = (a, annot_dt)
        else:
            _, existing_dt = latest_by_doc_map[a.document_id]
            if annot_dt > existing_dt:
                latest_by_doc_map[a.document_id] = (a, annot_dt)

    latest_per_doc_list = [val[0] for val in latest_by_doc_map.values()]

    # 4. Optional fresh_dispatch cohort (dispatch-window filtering join)
    # "latest-per-doc outcomes whose source alert was dispatched inside the requested time window."
    # Rule: De-duplicate all outcomes globally first (so we don't miss re-evaluations),
    # then join with audit, and filter by dispatched_at.
    global_latest_by_doc_map: dict[str, tuple[AlertOutcomeAnnotation, datetime]] = {}
    for a in outcomes:
        annot_dt = parse_utc_timestamp(a.annotated_at) or datetime.fromtimestamp(0, tz=timezone.utc)
        if a.document_id not in global_latest_by_doc_map:
            global_latest_by_doc_map[a.document_id] = (a, annot_dt)
        else:
            _, existing_dt = global_latest_by_doc_map[a.document_id]
            if annot_dt > existing_dt:
                global_latest_by_doc_map[a.document_id] = (a, annot_dt)

    fresh_dispatch_list: list[AlertOutcomeAnnotation] = []
    missing_audit = 0

    for doc_id, (a, _) in global_latest_by_doc_map.items():
        audit_info = audits_by_doc.get(doc_id)
        if audit_info is None:
            missing_audit += 1
            continue

        _, dispatch_dt = audit_info
        # Filter by dispatch time window
        if since_utc is not None and dispatch_dt < since_utc:
            continue
        if until_utc is not None and dispatch_dt > until_utc:
            continue

        fresh_dispatch_list.append(a)

    # 5. Build final report shape
    report = {
        "window": {
            "since": since.isoformat() if since is not None else None,
            "until": until.isoformat() if until is not None else None,
            "timestamp_basis": "dispatched_at" if use_dispatched_at else "annotated_at",
        },
        "raw_rows": len(filtered_outcomes),
        "invalid_timestamp": invalid_timestamp,
        "cohorts": {
            "fresh_auto": calculate_cohort_counters(fresh_auto_list),
            "backfill": calculate_cohort_counters(backfill_list),
            "reeval": calculate_cohort_counters(reeval_list),
            "other": calculate_cohort_counters(other_list),
            "latest_per_doc": {
                **calculate_cohort_counters(latest_per_doc_list),
                "raw_rows": len(filtered_outcomes),
                "unique_document_ids": len(latest_per_doc_list),
                "duplicate_rows_removed": len(filtered_outcomes) - len(latest_per_doc_list),
            },
            "fresh_dispatch": {
                **calculate_cohort_counters(fresh_dispatch_list),
                "missing_audit": missing_audit,
            },
        },
    }

    return report
