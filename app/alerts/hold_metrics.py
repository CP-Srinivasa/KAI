"""PH5 strategic hold metrics helpers.

This module computes and writes evidence snapshots used by the Phase-5 hold gate.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.alerts.audit import load_alert_audits, load_outcome_annotations

MIN_RESOLVED_DIRECTIONAL_ALERTS = 50
MIN_PAPER_CYCLES = 10
MIN_PAPER_FILLS = 3

HOLD_REPORT_JSON = "ph5_hold_metrics_report.json"
HOLD_REPORT_MD = "ph5_hold_operator_summary.md"


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                rows.append(parsed)
    return rows


def _latest_value(rows: list[dict[str, Any]], key: str) -> str | None:
    values = [
        row[key]
        for row in rows
        if key in row and isinstance(row[key], str) and row[key].strip()
    ]
    return max(values) if values else None


def build_hold_metrics_report(
    *,
    alert_audit_path: Path,
    alert_outcomes_path: Path,
    trading_loop_audit_path: Path,
    paper_execution_audit_path: Path,
) -> dict[str, Any]:
    """Build an in-memory PH5 hold metrics report from artifact paths."""
    audits = load_alert_audits(alert_audit_path)
    annotations = load_outcome_annotations(alert_outcomes_path)

    non_digest = [r for r in audits if not r.is_digest]
    directional = [
        r
        for r in non_digest
        if (r.sentiment_label or "").lower() in {"bullish", "bearish"}
    ]
    directional_doc_ids = {r.document_id for r in directional}

    # Alert audits are channel-level (email + telegram). For gate evidence we
    # track unique document IDs as a proxy for unique directional alerts.
    known_priority_docs = {
        r.document_id for r in non_digest if r.priority is not None
    }
    high_priority_docs = {
        r.document_id
        for r in non_digest
        if r.priority is not None and r.priority >= 7
    }

    latest_ann_by_doc: dict[str, str] = {}
    for ann in annotations:
        latest_ann_by_doc[ann.document_id] = ann.outcome

    labeled_directional_docs = {
        doc_id
        for doc_id in directional_doc_ids
        if doc_id in latest_ann_by_doc
    }
    hit_docs = {
        doc_id
        for doc_id in directional_doc_ids
        if latest_ann_by_doc.get(doc_id) == "hit"
    }
    miss_docs = {
        doc_id
        for doc_id in directional_doc_ids
        if latest_ann_by_doc.get(doc_id) == "miss"
    }
    resolved_docs = hit_docs | miss_docs
    inconclusive_docs = {
        doc_id
        for doc_id in directional_doc_ids
        if latest_ann_by_doc.get(doc_id) == "inconclusive"
    }
    hit_rate = (
        round(len(hit_docs) / len(resolved_docs) * 100.0, 2)
        if resolved_docs
        else None
    )

    loop_rows = _load_jsonl(trading_loop_audit_path)
    loop_status_counts = Counter(
        row.get("status", "unknown")
        for row in loop_rows
    )
    signal_generated_count = sum(
        1 for row in loop_rows if bool(row.get("signal_generated"))
    )
    risk_approved_count = sum(
        1 for row in loop_rows if bool(row.get("risk_approved"))
    )
    fill_simulated_count = sum(
        1 for row in loop_rows if bool(row.get("fill_simulated"))
    )

    exec_rows = _load_jsonl(paper_execution_audit_path)
    exec_event_counts = Counter(
        row.get("event_type", "unknown")
        for row in exec_rows
    )
    order_created_count = exec_event_counts.get("order_created", 0)
    order_filled_count = exec_event_counts.get("order_filled", 0)
    latest_realized_pnl = None
    for row in reversed(exec_rows):
        if "realized_pnl_usd" in row:
            try:
                latest_realized_pnl = float(row["realized_pnl_usd"])
            except (TypeError, ValueError):
                latest_realized_pnl = None
            break

    alert_hit_rate_condition_met = (
        len(resolved_docs) >= MIN_RESOLVED_DIRECTIONAL_ALERTS
    )

    # Conservative evidence condition: enough cycles + fills and non-negative
    # realized PnL when available.
    paper_trading_condition_met = (
        len(loop_rows) >= MIN_PAPER_CYCLES
        and order_filled_count >= MIN_PAPER_FILLS
        and (latest_realized_pnl is None or latest_realized_pnl >= 0)
    )

    by_channel = Counter(r.channel for r in audits)
    coverage_ratio = (
        round(len(labeled_directional_docs) / len(directional_doc_ids), 4)
        if directional_doc_ids
        else 0.0
    )
    priority_coverage = (
        round(len(known_priority_docs) / len({r.document_id for r in non_digest}), 4)
        if non_digest
        else 0.0
    )

    generated_at = datetime.now(UTC).isoformat()
    unique_alerted_docs = len({r.document_id for r in non_digest})
    return {
        "report_type": "ph5_hold_metrics_report",
        "phase": "PHASE 5",
        "generated_at": generated_at,
        "inputs": {
            "alert_audit_path": str(alert_audit_path),
            "alert_outcomes_path": str(alert_outcomes_path),
            "trading_loop_audit_path": str(trading_loop_audit_path),
            "paper_execution_audit_path": str(paper_execution_audit_path),
        },
        "alert_dispatch_summary": {
            "total_dispatch_events": len(audits),
            "non_digest_dispatch_events": len(non_digest),
            "unique_alerted_documents": unique_alerted_docs,
            "by_channel": dict(by_channel),
            "latest_dispatched_at": _latest_value(
                [r.to_json_dict() for r in audits], "dispatched_at"
            ),
        },
        "alert_hit_rate_evidence": {
            "finding": (
                "calculable" if alert_hit_rate_condition_met else "insufficient_data"
            ),
            "minimum_resolved_directional_alerts_for_gate": MIN_RESOLVED_DIRECTIONAL_ALERTS,
            "directional_alert_documents": len(directional_doc_ids),
            "labeled_directional_documents": len(labeled_directional_docs),
            "resolved_directional_documents": len(resolved_docs),
            "inconclusive_directional_documents": len(inconclusive_docs),
            "label_coverage_ratio": coverage_ratio,
            "alert_hits": len(hit_docs),
            "alert_misses": len(miss_docs),
            "alert_hit_rate": hit_rate,
            "calculable_for_gate": alert_hit_rate_condition_met,
        },
        "alert_precision_evidence": {
            "finding": "partial",
            "high_priority_threshold": 7,
            "unique_alerted_documents": unique_alerted_docs,
            "known_priority_documents": len(known_priority_docs),
            "unknown_priority_documents": max(0, unique_alerted_docs - len(known_priority_docs)),
            "priority_coverage_ratio": priority_coverage,
            "high_priority_documents": len(high_priority_docs),
            "alert_precision_proxy": (
                round(len(high_priority_docs) / len(known_priority_docs), 4)
                if known_priority_docs
                else None
            ),
        },
        "paper_trading_evidence": {
            "finding": (
                "clearly_positive" if paper_trading_condition_met else "insufficient_data"
            ),
            "minimum_cycles_for_gate": MIN_PAPER_CYCLES,
            "minimum_fills_for_gate": MIN_PAPER_FILLS,
            "loop_metrics": {
                "total_cycles": len(loop_rows),
                "status_counts": dict(loop_status_counts),
                "signal_generated_count": signal_generated_count,
                "risk_approved_count": risk_approved_count,
                "fill_simulated_count": fill_simulated_count,
                "latest_cycle_completed_at": _latest_value(loop_rows, "completed_at"),
            },
            "execution_metrics": {
                "total_events": len(exec_rows),
                "event_counts": dict(exec_event_counts),
                "order_created_count": order_created_count,
                "order_filled_count": order_filled_count,
                "latest_realized_pnl_usd": latest_realized_pnl,
            },
        },
        "hold_gate_evaluation": {
            "alert_hit_rate_condition_met": alert_hit_rate_condition_met,
            "paper_trading_condition_met": paper_trading_condition_met,
            "feature_work_unblocked": (
                alert_hit_rate_condition_met and paper_trading_condition_met
            ),
            "overall_status": (
                "hold_releasable"
                if (alert_hit_rate_condition_met and paper_trading_condition_met)
                else "hold_remains_active"
            ),
            "blocking_reasons": [
                reason
                for reason, is_blocking in [
                    (
                        "alert_hit_rate_not_calculable_50_plus",
                        not alert_hit_rate_condition_met,
                    ),
                    (
                        "paper_trading_not_clearly_positive",
                        not paper_trading_condition_met,
                    ),
                ]
                if is_blocking
            ],
        },
    }


def write_hold_metrics_report(
    report: dict[str, Any],
    *,
    output_dir: Path,
) -> tuple[Path, Path]:
    """Write report JSON + operator summary markdown and return both paths."""
    output_dir.mkdir(parents=True, exist_ok=True)
    json_out = output_dir / HOLD_REPORT_JSON
    md_out = output_dir / HOLD_REPORT_MD

    json_out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    _write_operator_summary(report, md_out)
    return json_out, md_out


def _write_operator_summary(report: dict[str, Any], output_path: Path) -> None:
    gate = report["hold_gate_evaluation"]
    hit = report["alert_hit_rate_evidence"]
    prec = report["alert_precision_evidence"]
    paper = report["paper_trading_evidence"]
    lines = [
        "# PH5 Strategic Hold Metrics - Operator Summary",
        "",
        f"Generated: {report['generated_at']}",
        "",
        "## Hold Gate Status",
        "",
        f"- overall_status: `{gate['overall_status']}`",
        f"- feature_work_unblocked: `{gate['feature_work_unblocked']}`",
        "- blocking_reasons: `" + ", ".join(gate["blocking_reasons"]) + "`",
        "",
        "## Alert Hit-Rate Evidence",
        "",
        f"- finding: `{hit['finding']}`",
        f"- directional_alert_documents: {hit['directional_alert_documents']}",
        f"- labeled_directional_documents: {hit['labeled_directional_documents']}",
        f"- resolved_directional_documents: {hit['resolved_directional_documents']}",
        "- minimum_resolved_directional_alerts_for_gate: "
        f"{hit['minimum_resolved_directional_alerts_for_gate']}",
        f"- alert_hit_rate: {hit['alert_hit_rate']}",
        "",
        "## Alert Precision Proxy",
        "",
        f"- known_priority_documents: {prec['known_priority_documents']}",
        f"- high_priority_documents: {prec['high_priority_documents']}",
        f"- alert_precision_proxy: {prec['alert_precision_proxy']}",
        "",
        "## Paper Trading Evidence",
        "",
        f"- finding: `{paper['finding']}`",
        f"- total_cycles: {paper['loop_metrics']['total_cycles']}",
        f"- fill_simulated_count: {paper['loop_metrics']['fill_simulated_count']}",
        f"- order_filled_count: {paper['execution_metrics']['order_filled_count']}",
        f"- latest_realized_pnl_usd: {paper['execution_metrics']['latest_realized_pnl_usd']}",
        "",
        "## Notes",
        "",
        "- Alert audit is channel-level; directional sample is deduplicated by document_id.",
        "- This report is evidence-tracking only and never lifts hold automatically.",
    ]
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

