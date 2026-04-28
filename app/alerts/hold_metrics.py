"""PH5 strategic hold metrics helpers.

This module computes and writes evidence snapshots used by the Phase-5 hold gate.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.alerts.audit import load_alert_audits, load_outcome_annotations
from app.alerts.eligibility import evaluate_directional_eligibility
from app.alerts.provenance_metrics import wilson_ci

# D-151 (2026-04-18): Gate now enforces sample-size AND active-precision.
# Raised from 50 to 200 to match the sprint-plan re-entry rule and to get the
# CI width under ~±7pp.  Precision threshold 60% reflects D-146 active-split
# (61.47% at snapshot) — any regression below this means the eligibility gate
# is losing ground and PHASE 5 work must stay on hold.  "Active" excludes
# pre-D-139 legacy_unknown docs (see LEGACY_UNKNOWN_CUTOFF below).
MIN_RESOLVED_DIRECTIONAL_ALERTS = 200
MIN_ACTIVE_PRECISION_PCT = 60.0
MIN_PAPER_CYCLES = 10
MIN_PAPER_FILLS = 3

# 2026-04-25: Per-source active-precision floor. Aggregate active-precision
# can be inflated by one dominant source while the rest of the sources
# under-perform; the per-source floor closes that loophole. The gate now
# requires at least one source with n>=50 resolved active outcomes AND a
# Wilson-95 lower bound >= 55% on its hit-rate. Wilson lower (not the point
# estimate) is the bar so a small-but-lucky source cannot release the gate.
MIN_PER_SOURCE_RESOLVED = 50
MIN_PER_SOURCE_WILSON_LOW_PCT = 55.0

# Stability check on top of the per-source floor. The same Wilson-lower
# threshold must hold across STABILITY_WINDOW_COUNT consecutive
# STABILITY_WINDOW_DAYS-day rolling windows. Per-window n is relaxed to
# MIN_PER_SOURCE_RESOLVED_PER_WINDOW so we can detect drift even when the
# 90-day total only marginally clears the headline 50-sample floor — but
# windows below that minimum are explicitly recorded as ``insufficient_n``
# rather than counting as a pass.
STABILITY_WINDOW_DAYS = 30
STABILITY_WINDOW_COUNT = 3
MIN_PER_SOURCE_RESOLVED_PER_WINDOW = 20

# D-139 fix (2026-03-28) stopped the bug that left directional docs without a
# persisted CanonicalDocument row — provenance buckets those as
# ``source=unknown``. They inflate the miss column without reflecting current
# pipeline behaviour. "Active" precision excludes this bucket when a
# source_by_doc lookup is provided; if no lookup is available we fall back to
# a dispatched_at cutoff so the dashboard still gets *some* signal without a
# DB hop.
LEGACY_UNKNOWN_SOURCE = "unknown"
LEGACY_UNKNOWN_CUTOFF = "2026-03-29"
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


def _parse_iso_utc(value: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp; return tz-aware UTC or None on failure."""
    if not isinstance(value, str) or not value:
        return None
    try:
        ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC)


def _wilson_low_pct(hits: int, total: int) -> float | None:
    if total <= 0:
        return None
    ci = wilson_ci(hits, total)
    if ci is None:
        return None
    return round(ci[0] * 100.0, 2)


def _wilson_high_pct(hits: int, total: int) -> float | None:
    if total <= 0:
        return None
    ci = wilson_ci(hits, total)
    if ci is None:
        return None
    return round(ci[1] * 100.0, 2)


def compute_per_source_active_precision(
    *,
    active_resolved_docs: set[str],
    hit_docs: set[str],
    source_lookup: dict[str, str],
) -> dict[str, dict[str, Any]]:
    """Per-source hits/misses/Wilson-CI on the active (post-D-139) bucket.

    Excludes ``LEGACY_UNKNOWN_SOURCE`` — the per-source floor is meant to
    measure *current* pipeline quality, and the legacy-unknown bucket
    represents the pre-D-139 attribution gap that the active-filter
    already excludes from the headline figure.

    Returns ``{source: {resolved, hits, misses, hit_rate_pct, ci_low_pct,
    ci_high_pct, n_threshold_met, wilson_low_threshold_met, passes_gate}}``.
    A source ``passes_gate`` iff resolved >= MIN_PER_SOURCE_RESOLVED AND
    ci_low_pct >= MIN_PER_SOURCE_WILSON_LOW_PCT.
    """
    counts: dict[str, dict[str, int]] = {}
    for doc_id in active_resolved_docs:
        source = (source_lookup.get(doc_id) or LEGACY_UNKNOWN_SOURCE).strip().lower()
        if source == LEGACY_UNKNOWN_SOURCE:
            continue
        bucket = counts.setdefault(source, {"hits": 0, "misses": 0})
        if doc_id in hit_docs:
            bucket["hits"] += 1
        else:
            bucket["misses"] += 1

    out: dict[str, dict[str, Any]] = {}
    for source, c in sorted(counts.items()):
        hits = c["hits"]
        misses = c["misses"]
        n = hits + misses
        ci_low = _wilson_low_pct(hits, n)
        ci_high = _wilson_high_pct(hits, n)
        n_ok = n >= MIN_PER_SOURCE_RESOLVED
        wilson_ok = ci_low is not None and ci_low >= MIN_PER_SOURCE_WILSON_LOW_PCT
        out[source] = {
            "resolved": n,
            "hits": hits,
            "misses": misses,
            "hit_rate_pct": _rate_pct(hits, n),
            "ci_low_pct": ci_low,
            "ci_high_pct": ci_high,
            "n_threshold_met": n_ok,
            "wilson_low_threshold_met": wilson_ok,
            "passes_gate": n_ok and wilson_ok,
        }
    return out


def compute_per_source_stability(
    *,
    active_resolved_docs: set[str],
    hit_docs: set[str],
    latest_directional_by_doc: dict[str, Any],
    source_lookup: dict[str, str],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Per-source × N rolling 30-day windows: Wilson-lower vs. threshold.

    Window 0 is the most recent ``STABILITY_WINDOW_DAYS`` days
    (``[now - 30d, now)``), window 1 is the 30-day block before that, and
    so on for ``STABILITY_WINDOW_COUNT`` total windows.

    A source is ``stable`` iff every window it has data in passes the
    Wilson-lower threshold AND meets the per-window minimum n. Windows
    below the per-window minimum are flagged ``insufficient_n`` and count
    as a fail — otherwise a source with one strong window and two empty
    windows would falsely qualify.
    """
    anchor = now or datetime.now(UTC)
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=UTC)
    boundaries: list[tuple[datetime, datetime]] = []
    for i in range(STABILITY_WINDOW_COUNT):
        end = anchor - timedelta(days=STABILITY_WINDOW_DAYS * i)
        start = end - timedelta(days=STABILITY_WINDOW_DAYS)
        boundaries.append((start, end))

    per_source_windows: dict[str, list[dict[str, Any]]] = {}
    for doc_id in active_resolved_docs:
        rec = latest_directional_by_doc.get(doc_id)
        if rec is None:
            continue
        ts = _parse_iso_utc(getattr(rec, "dispatched_at", None))
        if ts is None:
            continue
        source = (source_lookup.get(doc_id) or LEGACY_UNKNOWN_SOURCE).strip().lower()
        if source == LEGACY_UNKNOWN_SOURCE:
            continue
        windows = per_source_windows.setdefault(
            source,
            [
                {"hits": 0, "misses": 0, "start": s, "end": e}
                for s, e in boundaries
            ],
        )
        for window in windows:
            if window["start"] <= ts < window["end"]:
                if doc_id in hit_docs:
                    window["hits"] += 1
                else:
                    window["misses"] += 1
                break

    by_source: dict[str, dict[str, Any]] = {}
    for source, windows in sorted(per_source_windows.items()):
        window_results: list[dict[str, Any]] = []
        all_pass = True
        for window in windows:
            n = window["hits"] + window["misses"]
            ci_low = _wilson_low_pct(window["hits"], n)
            n_ok = n >= MIN_PER_SOURCE_RESOLVED_PER_WINDOW
            wilson_ok = (
                ci_low is not None and ci_low >= MIN_PER_SOURCE_WILSON_LOW_PCT
            )
            window_pass = n_ok and wilson_ok
            if not window_pass:
                all_pass = False
            window_results.append(
                {
                    "window_start": window["start"].isoformat(),
                    "window_end": window["end"].isoformat(),
                    "resolved": n,
                    "hits": window["hits"],
                    "misses": window["misses"],
                    "hit_rate_pct": _rate_pct(window["hits"], n),
                    "ci_low_pct": ci_low,
                    "n_threshold_met": n_ok,
                    "wilson_low_threshold_met": wilson_ok,
                    "passes_window": window_pass,
                    "fail_reason": (
                        None
                        if window_pass
                        else ("insufficient_n" if not n_ok else "wilson_low_below_threshold")
                    ),
                }
            )
        by_source[source] = {"windows": window_results, "stable": all_pass}

    return {
        "window_days": STABILITY_WINDOW_DAYS,
        "window_count": STABILITY_WINDOW_COUNT,
        "min_resolved_per_window": MIN_PER_SOURCE_RESOLVED_PER_WINDOW,
        "min_wilson_low_pct": MIN_PER_SOURCE_WILSON_LOW_PCT,
        "anchor_at": anchor.isoformat(),
        "by_source": by_source,
    }


def build_hold_metrics_report(
    *,
    alert_audit_path: Path,
    alert_outcomes_path: Path,
    trading_loop_audit_path: Path,
    paper_execution_audit_path: Path,
    source_by_doc: dict[str, str] | None = None,
    title_by_doc: dict[str, str] | None = None,
    stability_anchor: datetime | None = None,
) -> dict[str, Any]:
    """Build an in-memory PH5 hold metrics report from artifact paths.

    ``stability_anchor`` pins the rolling-window endpoint for the
    per-source stability check. Defaults to ``datetime.now(UTC)``.
    Tests pass a fixed anchor so window boundaries are deterministic.
    """
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

        # D-142: Always re-evaluate eligibility against current rules.
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

    # Active-subset: excludes legacy-unknown docs (directional docs that have
    # no persisted CanonicalDocument row, a pre-D-139 artefact). When a
    # source_by_doc lookup is provided we filter exactly on source=unknown;
    # without a lookup we fall back to a conservative date cutoff.
    _source_lookup = source_by_doc or {}

    def _is_active(doc_id: str) -> bool:
        if _source_lookup:
            src = (_source_lookup.get(doc_id) or LEGACY_UNKNOWN_SOURCE).strip().lower()
            return src != LEGACY_UNKNOWN_SOURCE
        rec = latest_directional_by_doc.get(doc_id)
        if rec is None or not rec.dispatched_at:
            return False
        return rec.dispatched_at >= LEGACY_UNKNOWN_CUTOFF

    active_hit_docs = {d for d in hit_docs if _is_active(d)}
    active_miss_docs = {d for d in miss_docs if _is_active(d)}
    active_resolved_docs = active_hit_docs | active_miss_docs
    active_hit_rate = _rate_pct(len(active_hit_docs), len(active_resolved_docs))
    active_false_positive_rate = _rate_pct(
        len(active_miss_docs), len(active_resolved_docs)
    )
    legacy_resolved_count = len(resolved_docs) - len(active_resolved_docs)
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

    # D-151: active-precision gate — excludes pre-D-139 legacy_unknown docs so
    # the Altlasten-bucket cannot suppress the measurement.  None counts as
    # unmet (no active sample yet).
    active_precision_condition_met = (
        active_hit_rate is not None
        and active_hit_rate >= MIN_ACTIVE_PRECISION_PCT
    )

    # 2026-04-25: Per-source floor + 3-window stability. Both must pass for
    # the gate to release; the source that clears the precision floor must
    # also be the one that demonstrates stability (intersection check
    # below). Active-only — legacy_unknown bucket is excluded inside the
    # helpers, matching the headline active-precision filter.
    per_source_active_precision = compute_per_source_active_precision(
        active_resolved_docs=active_resolved_docs,
        hit_docs=active_hit_docs,
        source_lookup=_source_lookup,
    )
    per_source_stability = compute_per_source_stability(
        active_resolved_docs=active_resolved_docs,
        hit_docs=active_hit_docs,
        latest_directional_by_doc=latest_directional_by_doc,
        source_lookup=_source_lookup,
        now=stability_anchor,
    )
    sources_passing_precision: list[str] = sorted(
        src for src, m in per_source_active_precision.items() if m["passes_gate"]
    )
    sources_stable: set[str] = {
        src
        for src, info in per_source_stability["by_source"].items()
        if info["stable"]
    }
    sources_passing_both: list[str] = sorted(
        set(sources_passing_precision) & sources_stable
    )
    per_source_precision_condition_met = bool(sources_passing_precision)
    per_source_stability_condition_met = bool(sources_passing_both)

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
    # D-149: Priority tier analysis (P10 vs P7-P9) replaces linear correlation
    # as the primary priority-calibration signal.  Rationale: within the
    # post-gate P7-P10 band, hit-rate is non-monotonic (P7≈35%, P9≈22%,
    # P10≈54%), so Pearson ≈ 0 even though P10 carries real signal.
    high_conviction_tier_threshold = 10
    high_conviction_resolved_docs: set[str] = set()
    standard_tier_resolved_docs: set[str] = set()
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
        if latest_record.priority >= high_conviction_tier_threshold:
            high_conviction_resolved_docs.add(doc_id)
        elif latest_record.priority >= high_priority_threshold:
            standard_tier_resolved_docs.add(doc_id)

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

    # D-149: tier precision + Wilson 95% CI per tier.
    def _tier_stats(docs: set[str]) -> dict[str, Any]:
        hits = sum(1 for d in docs if d in hit_docs)
        n = len(docs)
        ci = wilson_ci(hits, n) if n > 0 else None
        return {
            "resolved": n,
            "hits": hits,
            "hit_rate_pct": _rate_pct(hits, n),
            "ci_low_pct": round(ci[0] * 100.0, 2) if ci is not None else None,
            "ci_high_pct": round(ci[1] * 100.0, 2) if ci is not None else None,
        }

    high_conviction_stats = _tier_stats(high_conviction_resolved_docs)
    standard_tier_stats = _tier_stats(standard_tier_resolved_docs)
    priority_tier_lift_pct: float | None = None
    if (
        high_conviction_stats["hit_rate_pct"] is not None
        and standard_tier_stats["hit_rate_pct"] is not None
    ):
        priority_tier_lift_pct = round(
            high_conviction_stats["hit_rate_pct"]
            - standard_tier_stats["hit_rate_pct"],
            2,
        )

    # D-149: `priority_calibration_finding` + `priority_hit_correlation` are
    # retained for backwards-compat but deprecated as a tuning signal.  Pearson
    # linear correlation is not the right statistic inside the narrow P7-P10
    # post-gate band; use priority_tier_* fields instead.  The finding value
    # below remains computed so existing callers don't break.
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
    priority_hit_correlation_deprecated_reason = (
        "non_monotonic_within_p7_p10_band_see_d149"
    )

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
            "legacy_unknown_cutoff": LEGACY_UNKNOWN_CUTOFF,
            "active_resolved_directional_documents": len(active_resolved_docs),
            "active_alert_hits": len(active_hit_docs),
            "active_alert_misses": len(active_miss_docs),
            "legacy_resolved_documents": legacy_resolved_count,
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
            "active_precision_pct": active_hit_rate,
            "active_false_positive_rate_pct": active_false_positive_rate,
            "resolved_recall_pct": None,
            "recall_computable": False,
            "feedback_loop_labeled_ratio": coverage_ratio,
            "feedback_loop_resolved_ratio": resolved_coverage_ratio,
            "priority_calibration_finding": priority_calibration_finding,
            "priority_hit_correlation": priority_corr,
            "priority_hit_correlation_sample": len(priority_hits_pairs),
            "priority_hit_correlation_deprecated_reason": (
                priority_hit_correlation_deprecated_reason
            ),
            "high_priority_threshold": high_priority_threshold,
            "high_priority_resolved_documents": len(high_priority_resolved_docs),
            "high_priority_hit_rate_pct": high_priority_hit_rate,
            "low_priority_resolved_documents": len(low_priority_resolved_docs),
            "low_priority_hit_rate_pct": low_priority_hit_rate,
            # D-149: tier-based priority calibration (preferred over correlation)
            "priority_tier_high_conviction_threshold": high_conviction_tier_threshold,
            "priority_tier_high_conviction_resolved": high_conviction_stats["resolved"],
            "priority_tier_high_conviction_hit_rate_pct": high_conviction_stats["hit_rate_pct"],
            "priority_tier_high_conviction_ci_low_pct": high_conviction_stats["ci_low_pct"],
            "priority_tier_high_conviction_ci_high_pct": high_conviction_stats["ci_high_pct"],
            "priority_tier_standard_resolved": standard_tier_stats["resolved"],
            "priority_tier_standard_hit_rate_pct": standard_tier_stats["hit_rate_pct"],
            "priority_tier_standard_ci_low_pct": standard_tier_stats["ci_low_pct"],
            "priority_tier_standard_ci_high_pct": standard_tier_stats["ci_high_pct"],
            "priority_tier_lift_pct": priority_tier_lift_pct,
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
        "per_source_active_precision": {
            "min_resolved": MIN_PER_SOURCE_RESOLVED,
            "min_wilson_low_pct": MIN_PER_SOURCE_WILSON_LOW_PCT,
            "by_source": per_source_active_precision,
            "sources_passing": sources_passing_precision,
        },
        "per_source_stability": per_source_stability,
        "hold_gate_evaluation": {
            "alert_hit_rate_condition_met": alert_hit_rate_condition_met,
            "active_precision_condition_met": active_precision_condition_met,
            "per_source_precision_condition_met": per_source_precision_condition_met,
            "per_source_stability_condition_met": per_source_stability_condition_met,
            "sources_passing_both": sources_passing_both,
            "paper_trading_condition_met": paper_trading_condition_met,
            "minimum_resolved_directional_alerts_for_gate": (
                MIN_RESOLVED_DIRECTIONAL_ALERTS
            ),
            "minimum_active_precision_pct_for_gate": MIN_ACTIVE_PRECISION_PCT,
            "minimum_per_source_resolved_for_gate": MIN_PER_SOURCE_RESOLVED,
            "minimum_per_source_wilson_low_pct_for_gate": (
                MIN_PER_SOURCE_WILSON_LOW_PCT
            ),
            "feature_work_unblocked": (
                alert_hit_rate_condition_met
                and active_precision_condition_met
                and per_source_precision_condition_met
                and per_source_stability_condition_met
                and paper_trading_condition_met
            ),
            "overall_status": (
                "hold_releasable"
                if (
                    alert_hit_rate_condition_met
                    and active_precision_condition_met
                    and per_source_precision_condition_met
                    and per_source_stability_condition_met
                    and paper_trading_condition_met
                )
                else "hold_remains_active"
            ),
            "blocking_reasons": [
                reason
                for reason, is_blocking in [
                    (
                        f"resolved_directional_below_{MIN_RESOLVED_DIRECTIONAL_ALERTS}",
                        not alert_hit_rate_condition_met,
                    ),
                    (
                        f"active_precision_below_{int(MIN_ACTIVE_PRECISION_PCT)}_pct",
                        not active_precision_condition_met,
                    ),
                    (
                        (
                            "no_source_meets_per_source_floor_n"
                            f"{MIN_PER_SOURCE_RESOLVED}"
                            f"_wilson_low{int(MIN_PER_SOURCE_WILSON_LOW_PCT)}pct"
                        ),
                        not per_source_precision_condition_met,
                    ),
                    (
                        (
                            "no_source_stable_across_"
                            f"{STABILITY_WINDOW_COUNT}x"
                            f"{STABILITY_WINDOW_DAYS}d_windows"
                        ),
                        not per_source_stability_condition_met,
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
        f"- alert_hit_rate_condition_met: `{gate['alert_hit_rate_condition_met']}` "
        f"(min resolved={gate['minimum_resolved_directional_alerts_for_gate']})",
        f"- active_precision_condition_met: `{gate['active_precision_condition_met']}` "
        f"(min active precision={gate['minimum_active_precision_pct_for_gate']}%)",
        f"- paper_trading_condition_met: `{gate['paper_trading_condition_met']}`",
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
        "- priority_calibration_finding (DEPRECATED, see D-149): "
        f"`{quality['priority_calibration_finding']}`",
        "- priority_hit_correlation (DEPRECATED, non-monotonic): "
        f"{quality['priority_hit_correlation']} "
        f"(n={quality['priority_hit_correlation_sample']})",
        f"- high_priority_hit_rate_pct: {quality['high_priority_hit_rate_pct']}",
        f"- low_priority_hit_rate_pct: {quality['low_priority_hit_rate_pct']}",
        "### Priority Tier Calibration (D-149, preferred)",
        "- priority_tier_high_conviction "
        f"(P>={quality['priority_tier_high_conviction_threshold']}): "
        f"n={quality['priority_tier_high_conviction_resolved']} "
        f"hit_rate={quality['priority_tier_high_conviction_hit_rate_pct']}% "
        f"CI95=["
        f"{quality['priority_tier_high_conviction_ci_low_pct']}, "
        f"{quality['priority_tier_high_conviction_ci_high_pct']}]",
        "- priority_tier_standard (P7-P9): "
        f"n={quality['priority_tier_standard_resolved']} "
        f"hit_rate={quality['priority_tier_standard_hit_rate_pct']}% "
        f"CI95=["
        f"{quality['priority_tier_standard_ci_low_pct']}, "
        f"{quality['priority_tier_standard_ci_high_pct']}]",
        f"- priority_tier_lift_pct: {quality['priority_tier_lift_pct']} "
        "(P10 minus P7-P9 hit-rate)",
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
