"""PH5 strategic hold metrics helpers.

This module computes and writes evidence snapshots used by the Phase-5 hold gate.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.alerts.audit import load_alert_audits, load_outcome_annotations
from app.alerts.eligibility import evaluate_directional_eligibility

MIN_RESOLVED_DIRECTIONAL_ALERTS = 50
MIN_PAPER_CYCLES = 10
MIN_PAPER_FILLS = 3
PRIORITY_MAE_BASELINE = 3.13
PRIORITY_MAE_BASELINE_DATE = "2026-03-23"
PRIORITY_MAE_BASELINE_DECISION = "D-57"
LLM_ERROR_PROXY_BASELINE_PCT = 27.5
LLM_ERROR_PROXY_BASELINE_SAMPLE = "19/69"
LLM_ERROR_PROXY_BASELINE_DATE = "2026-03-24"
LLM_ERROR_PROXY_BASELINE_DECISION = "D-101"

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


def _rate_pct(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator * 100.0, 2)


def _pearson_correlation(xs: list[float], ys: list[float]) -> float | None:
    """Return Pearson correlation or None when it is not computable."""
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=False))
    den_x = math.sqrt(sum((x - mean_x) ** 2 for x in xs))
    den_y = math.sqrt(sum((y - mean_y) ** 2 for y in ys))
    if den_x == 0.0 or den_y == 0.0:
        return None
    return round(num / (den_x * den_y), 4)


def build_hold_metrics_report(
    *,
    alert_audit_path: Path,
    alert_outcomes_path: Path,
    trading_loop_audit_path: Path,
    paper_execution_audit_path: Path,
    source_by_doc: dict[str, str] | None = None,
    title_by_doc: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build an in-memory PH5 hold metrics report from artifact paths."""
    audits = load_alert_audits(alert_audit_path)
    annotations = load_outcome_annotations(alert_outcomes_path)

    non_digest = [r for r in audits if not r.is_digest]
    directional: list[Any] = []
    blocked_directional: list[Any] = []
    blocked_directional_reasons: list[str] = []
    for rec in non_digest:
        sentiment = (rec.sentiment_label or "").lower()
        if sentiment not in {"bullish", "bearish"}:
            continue

        # D-127: Always re-evaluate eligibility against current rules.
        # Historical audit records may have directional_eligible=True under
        # older, weaker filters.  Re-checking ensures the hold report
        # reflects the current filter configuration (e.g. bearish disabled).
        current_check = evaluate_directional_eligibility(
            sentiment_label=rec.sentiment_label,
            affected_assets=list(rec.affected_assets or []),
        )
        if current_check.directional_eligible is True:
            # Also honour the original decision if it was False (stricter).
            if rec.directional_eligible is False:
                blocked_directional.append(rec)
                blocked_directional_reasons.append(
                    rec.directional_block_reason or "unknown"
                )
            else:
                directional.append(rec)
        else:
            blocked_directional.append(rec)
            blocked_directional_reasons.append(
                current_check.directional_block_reason or "unknown"
            )

    blocked_directional_reason_counts = Counter(blocked_directional_reasons)
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

    latest_directional_by_doc: dict[str, Any] = {}
    for rec in directional:
        prev = latest_directional_by_doc.get(rec.document_id)
        if prev is None or rec.dispatched_at > prev.dispatched_at:
            latest_directional_by_doc[rec.document_id] = rec

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
    actionable_directional_docs = {
        doc_id
        for doc_id, rec in latest_directional_by_doc.items()
        if rec.actionable is True
    }
    actionable_unknown_directional_docs = {
        doc_id
        for doc_id, rec in latest_directional_by_doc.items()
        if rec.actionable is None
    }
    hit_rate = _rate_pct(len(hit_docs), len(resolved_docs))
    false_positive_rate = _rate_pct(len(miss_docs), len(resolved_docs))
    actionable_rate = (
        round(len(actionable_directional_docs) / len(directional_doc_ids) * 100.0, 2)
        if directional_doc_ids
        else None
    )
    resolved_coverage_ratio = (
        round(len(resolved_docs) / len(directional_doc_ids), 4)
        if directional_doc_ids
        else 0.0
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
    market_data_source_counts: Counter[str] = Counter()
    latest_real_price_cycle_completed_at = None
    for row in loop_rows:
        notes = row.get("notes")
        if not isinstance(notes, list):
            continue
        completed_at = row.get("completed_at")
        for note in notes:
            if not isinstance(note, str) or not note.startswith("market_data_source:"):
                continue
            source = note.split(":", 1)[1].strip().lower() or "unknown"
            market_data_source_counts[source] += 1
            if source == "coingecko" and isinstance(completed_at, str):
                if (
                    latest_real_price_cycle_completed_at is None
                    or completed_at > latest_real_price_cycle_completed_at
                ):
                    latest_real_price_cycle_completed_at = completed_at
    real_price_cycle_count = market_data_source_counts.get("coingecko", 0)
    mock_price_cycle_count = market_data_source_counts.get("mock", 0)

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
    validation_gaps: list[str] = []
    if len(resolved_docs) < MIN_RESOLVED_DIRECTIONAL_ALERTS:
        validation_gaps.append("resolved_directional_below_gate")
    if real_price_cycle_count == 0:
        validation_gaps.append("no_real_price_paper_cycles")
    if order_filled_count == 0:
        validation_gaps.append("no_filled_paper_orders")
    # Recall requires a ground-truth negative universe that is not captured in
    # alert_audit/outcomes artifacts (only triggered-alert outcomes are stored).
    validation_gaps.append("recall_not_computable_without_negative_ground_truth")

    generated_at = datetime.now(UTC).isoformat()
    unique_alerted_docs = len({r.document_id for r in non_digest})

    high_priority_threshold = 7
    priority_hits_pairs: list[tuple[float, float]] = []
    high_priority_resolved_docs: set[str] = set()
    low_priority_resolved_docs: set[str] = set()
    for doc_id in resolved_docs:
        latest_record = latest_directional_by_doc.get(doc_id)
        if latest_record is None or latest_record.priority is None:
            continue
        priority_hits_pairs.append(
            (float(latest_record.priority), 1.0 if doc_id in hit_docs else 0.0)
        )
        if latest_record.priority >= high_priority_threshold:
            high_priority_resolved_docs.add(doc_id)
        else:
            low_priority_resolved_docs.add(doc_id)

    priority_corr = _pearson_correlation(
        [p for p, _ in priority_hits_pairs],
        [h for _, h in priority_hits_pairs],
    )
    high_priority_hit_rate = _rate_pct(
        sum(1 for d in high_priority_resolved_docs if d in hit_docs),
        len(high_priority_resolved_docs),
    )
    low_priority_hit_rate = _rate_pct(
        sum(1 for d in low_priority_resolved_docs if d in hit_docs),
        len(low_priority_resolved_docs),
    )
    if len(priority_hits_pairs) < 10:
        priority_calibration_finding = "insufficient_sample"
    elif priority_corr is None:
        priority_calibration_finding = "not_computable"
    elif priority_corr >= 0.2:
        priority_calibration_finding = "positive_correlation"
    elif priority_corr <= -0.2:
        priority_calibration_finding = "inverse_correlation"
    else:
        priority_calibration_finding = "weak_correlation"

    # D-134: Forward-precision simulation using all audit record fields.
    # Re-evaluates each resolved alert with current gates (priority,
    # actionable, bearish, source).
    fwd_hit_docs: set[str] = set()
    fwd_miss_docs: set[str] = set()
    fwd_priority_pairs: list[tuple[float, float]] = []
    for doc_id in resolved_docs:
        rec = latest_directional_by_doc.get(doc_id)
        if rec is None:
            continue
        # Prefer fields from audit record; fall back to DB lookup
        src = rec.source_name or (source_by_doc or {}).get(doc_id)
        ttl = rec.normalized_title or (title_by_doc or {}).get(doc_id)
        fwd_check = evaluate_directional_eligibility(
            sentiment_label=rec.sentiment_label,
            affected_assets=list(rec.affected_assets or []),
            priority=rec.priority,
            actionable=rec.actionable,
            source_name=src,
            title=ttl,
        )
        if fwd_check.directional_eligible is True:
            is_hit = doc_id in hit_docs
            if is_hit:
                fwd_hit_docs.add(doc_id)
            else:
                fwd_miss_docs.add(doc_id)
            if rec.priority is not None:
                fwd_priority_pairs.append(
                    (float(rec.priority), 1.0 if is_hit else 0.0),
                )
    fwd_resolved = len(fwd_hit_docs) + len(fwd_miss_docs)
    fwd_precision = _rate_pct(len(fwd_hit_docs), fwd_resolved)
    fwd_priority_corr = _pearson_correlation(
        [p for p, _ in fwd_priority_pairs],
        [h for _, h in fwd_priority_pairs],
    )

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
            "blocked_directional_documents": len(
                {r.document_id for r in blocked_directional}
            ),
            "blocked_directional_by_reason": dict(blocked_directional_reason_counts),
            "labeled_directional_documents": len(labeled_directional_docs),
            "resolved_directional_documents": len(resolved_docs),
            "inconclusive_directional_documents": len(inconclusive_docs),
            "label_coverage_ratio": coverage_ratio,
            "alert_hits": len(hit_docs),
            "alert_misses": len(miss_docs),
            "alert_hit_rate": hit_rate,
            "calculable_for_gate": alert_hit_rate_condition_met,
        },
        "forward_simulation": {
            "description": (
                "Re-evaluates resolved outcomes with current gates "
                "(priority, actionable, bearish, source)."
            ),
            "hits": len(fwd_hit_docs),
            "miss": len(fwd_miss_docs),
            "resolved": fwd_resolved,
            "filtered_out": len(resolved_docs) - fwd_resolved,
            "precision_pct": fwd_precision,
            "priority_hit_correlation": fwd_priority_corr,
            "priority_sample": len(fwd_priority_pairs),
        },
        "signal_quality_validation": {
            "directional_actionable_documents": len(actionable_directional_docs),
            "directional_actionable_unknown_documents": len(actionable_unknown_directional_docs),
            "directional_actionable_rate_pct": actionable_rate,
            "resolved_precision_pct": hit_rate,
            "resolved_false_positive_rate_pct": false_positive_rate,
            "resolved_recall_pct": None,
            "recall_computable": False,
            "feedback_loop_labeled_ratio": coverage_ratio,
            "feedback_loop_resolved_ratio": resolved_coverage_ratio,
            "priority_calibration_finding": priority_calibration_finding,
            "priority_hit_correlation": priority_corr,
            "priority_hit_correlation_sample": len(priority_hits_pairs),
            "high_priority_threshold": high_priority_threshold,
            "high_priority_resolved_documents": len(high_priority_resolved_docs),
            "high_priority_hit_rate_pct": high_priority_hit_rate,
            "low_priority_resolved_documents": len(low_priority_resolved_docs),
            "low_priority_hit_rate_pct": low_priority_hit_rate,
            "paper_market_data_source_counts": dict(market_data_source_counts),
            "paper_real_price_cycle_count": real_price_cycle_count,
            "paper_mock_price_cycle_count": mock_price_cycle_count,
            "latest_real_price_cycle_completed_at": latest_real_price_cycle_completed_at,
            "priority_mae_tier1_vs_teacher_baseline": PRIORITY_MAE_BASELINE,
            "priority_mae_baseline_date": PRIORITY_MAE_BASELINE_DATE,
            "priority_mae_baseline_decision": PRIORITY_MAE_BASELINE_DECISION,
            "llm_error_proxy_baseline_pct": LLM_ERROR_PROXY_BASELINE_PCT,
            "llm_error_proxy_baseline_sample": LLM_ERROR_PROXY_BASELINE_SAMPLE,
            "llm_error_proxy_baseline_date": LLM_ERROR_PROXY_BASELINE_DATE,
            "llm_error_proxy_baseline_decision": LLM_ERROR_PROXY_BASELINE_DECISION,
            "validation_gaps": validation_gaps,
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
    fwd = report.get("forward_simulation", {})
    quality = report["signal_quality_validation"]
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
        "## Forward Precision Simulation",
        "",
        f"- forward_precision_pct: {fwd.get('precision_pct')}",
        f"- forward_resolved: {fwd.get('resolved', 0)}",
        f"- forward_hits: {fwd.get('hits', 0)}",
        f"- forward_miss: {fwd.get('miss', 0)}",
        f"- filtered_out: {fwd.get('filtered_out', 0)}",
        f"- forward_priority_corr: {fwd.get('priority_hit_correlation')}",
        "",
        "## Signal-Quality Validation",
        "",
        f"- directional_actionable_rate_pct: {quality['directional_actionable_rate_pct']}",
        f"- resolved_precision_pct: {quality['resolved_precision_pct']}",
        f"- resolved_false_positive_rate_pct: {quality['resolved_false_positive_rate_pct']}",
        f"- resolved_recall_pct: {quality['resolved_recall_pct']}",
        f"- feedback_loop_resolved_ratio: {quality['feedback_loop_resolved_ratio']}",
        f"- priority_calibration_finding: {quality['priority_calibration_finding']}",
        f"- priority_hit_correlation: {quality['priority_hit_correlation']}",
        f"- priority_hit_correlation_sample: {quality['priority_hit_correlation_sample']}",
        f"- high_priority_hit_rate_pct: {quality['high_priority_hit_rate_pct']}",
        f"- low_priority_hit_rate_pct: {quality['low_priority_hit_rate_pct']}",
        f"- paper_real_price_cycle_count: {quality['paper_real_price_cycle_count']}",
        "- priority_mae_tier1_vs_teacher_baseline: "
        f"{quality['priority_mae_tier1_vs_teacher_baseline']}",
        f"- llm_error_proxy_baseline_pct: {quality['llm_error_proxy_baseline_pct']}",
        "- validation_gaps: `" + ", ".join(quality["validation_gaps"]) + "`",
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
