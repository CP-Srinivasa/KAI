"""TV-4 Quality-Bar — per-source precision metrics with Wilson confidence intervals.

Reads alert_audit + outcomes + TV pending-signals and produces a provenance-split
report. Each signal source (rss, tradingview_webhook, unknown, ...) gets its own
precision/hit-rate computation with a Wilson confidence interval, so operators
can judge whether the broader signal pipeline introduced by the TV-pivot
improves, degrades, or leaves precision unchanged compared to the RSS baseline.

Pure read-only projection — no side effects on audit or portfolio.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.alerts.audit import load_alert_audits, load_outcome_annotations

MIN_SAMPLE_FOR_JUDGMENT = 30  # Wilson CI stays wide below this; below → insufficient
DEFAULT_SOURCE = "unknown"


@dataclass(frozen=True)
class ProvenanceMetrics:
    """Per-source quality metrics with 95% Wilson confidence interval."""

    source: str
    resolved: int
    hits: int
    misses: int
    hit_rate_pct: float | None
    ci_low_pct: float | None
    ci_high_pct: float | None
    ci_width_pct: float | None
    sample_sufficient: bool


@dataclass(frozen=True)
class TradingViewSignalSummary:
    """TV-webhook pipeline summary. TV-events aren't labeled yet — audit only."""

    pending_events: int
    smoke_test_events: int
    real_events: int
    unique_signal_path_ids: int


@dataclass(frozen=True)
class ProvenanceReport:
    generated_at: str
    overall: ProvenanceMetrics
    by_source: list[ProvenanceMetrics]
    tradingview_pipeline: TradingViewSignalSummary
    verdict: str
    notes: list[str] = field(default_factory=list)


def wilson_ci(hits: int, total: int, z: float = 1.96) -> tuple[float, float] | None:
    """Return (low, high) of the Wilson score 95% confidence interval in [0, 1].

    Returns None if total <= 0. Wilson CI is preferred over normal approximation
    because it remains valid at small samples and near p=0 / p=1.
    """
    if total <= 0:
        return None
    p = hits / total
    denom = 1.0 + (z * z) / total
    center = (p + (z * z) / (2.0 * total)) / denom
    margin = (z * math.sqrt((p * (1.0 - p) + (z * z) / (4.0 * total)) / total)) / denom
    low = max(0.0, center - margin)
    high = min(1.0, center + margin)
    return low, high


def _compute_metrics(source: str, hits: int, misses: int) -> ProvenanceMetrics:
    resolved = hits + misses
    if resolved == 0:
        return ProvenanceMetrics(
            source=source,
            resolved=0,
            hits=0,
            misses=0,
            hit_rate_pct=None,
            ci_low_pct=None,
            ci_high_pct=None,
            ci_width_pct=None,
            sample_sufficient=False,
        )
    ci = wilson_ci(hits, resolved)
    low, high = ci if ci is not None else (None, None)
    return ProvenanceMetrics(
        source=source,
        resolved=resolved,
        hits=hits,
        misses=misses,
        hit_rate_pct=round(hits / resolved * 100.0, 2),
        ci_low_pct=round(low * 100.0, 2) if low is not None else None,
        ci_high_pct=round(high * 100.0, 2) if high is not None else None,
        ci_width_pct=(
            round((high - low) * 100.0, 2)
            if low is not None and high is not None
            else None
        ),
        sample_sufficient=resolved >= MIN_SAMPLE_FOR_JUDGMENT,
    )


def _summarize_tv_pipeline(tv_pending_path: Path) -> TradingViewSignalSummary:
    if not tv_pending_path.exists():
        return TradingViewSignalSummary(
            pending_events=0,
            smoke_test_events=0,
            real_events=0,
            unique_signal_path_ids=0,
        )
    pending = 0
    smoke = 0
    path_ids: set[str] = set()
    for raw in tv_pending_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        pending += 1
        note = (record.get("note") or "").lower()
        if "smoke" in note or "test" in note:
            smoke += 1
        provenance = record.get("provenance")
        if isinstance(provenance, dict):
            path_id = provenance.get("signal_path_id")
            if isinstance(path_id, str):
                path_ids.add(path_id)
    return TradingViewSignalSummary(
        pending_events=pending,
        smoke_test_events=smoke,
        real_events=pending - smoke,
        unique_signal_path_ids=len(path_ids),
    )


def _derive_verdict(
    overall: ProvenanceMetrics, by_source: list[ProvenanceMetrics]
) -> tuple[str, list[str]]:
    """Return (verdict_label, explanatory_notes)."""
    notes: list[str] = []

    rss_metrics = next((m for m in by_source if m.source == "rss"), None)
    tv_metrics = next((m for m in by_source if m.source == "tradingview_webhook"), None)

    if tv_metrics is None or tv_metrics.resolved == 0:
        notes.append(
            "tradingview_webhook: no resolved outcomes yet -- TV-pivot effect not measurable"
        )

    if rss_metrics is not None and not rss_metrics.sample_sufficient:
        notes.append(
            f"rss: resolved={rss_metrics.resolved} below threshold "
            f"{MIN_SAMPLE_FOR_JUDGMENT} — CI remains wide"
        )

    if (
        rss_metrics is None
        or tv_metrics is None
        or tv_metrics.resolved == 0
        or rss_metrics.resolved == 0
    ):
        return "insufficient_sample_for_split_comparison", notes

    if (
        rss_metrics.ci_high_pct is not None
        and tv_metrics.ci_low_pct is not None
        and tv_metrics.ci_low_pct > rss_metrics.ci_high_pct
    ):
        return "tv_significantly_better_than_rss", notes
    if (
        tv_metrics.ci_high_pct is not None
        and rss_metrics.ci_low_pct is not None
        and rss_metrics.ci_low_pct > tv_metrics.ci_high_pct
    ):
        return "rss_significantly_better_than_tv", notes
    return "overlapping_confidence_intervals_no_significant_difference", notes


def build_provenance_split_report(
    *,
    alert_audit_path: Path,
    alert_outcomes_path: Path,
    tradingview_pending_signals_path: Path,
    source_by_doc: dict[str, str] | None = None,
) -> ProvenanceReport:
    """Compute per-source precision with Wilson CI and summarize TV-pipeline."""
    audits = load_alert_audits(alert_audit_path)
    annotations = load_outcome_annotations(alert_outcomes_path)

    latest_outcome: dict[str, str] = {}
    for ann in annotations:
        latest_outcome[ann.document_id] = ann.outcome

    directional_docs: set[str] = set()
    for rec in audits:
        sentiment = (rec.sentiment_label or "").lower()
        if rec.is_digest or sentiment not in {"bullish", "bearish"}:
            continue
        directional_docs.add(rec.document_id)

    source_lookup = source_by_doc or {}

    total_hits = 0
    total_misses = 0
    per_source_hits: dict[str, int] = {}
    per_source_misses: dict[str, int] = {}

    for doc_id in directional_docs:
        outcome = latest_outcome.get(doc_id)
        if outcome not in {"hit", "miss"}:
            continue
        source = (source_lookup.get(doc_id) or DEFAULT_SOURCE).strip().lower()
        if outcome == "hit":
            total_hits += 1
            per_source_hits[source] = per_source_hits.get(source, 0) + 1
        else:
            total_misses += 1
            per_source_misses[source] = per_source_misses.get(source, 0) + 1

    overall = _compute_metrics("__overall__", total_hits, total_misses)

    all_sources = sorted(set(per_source_hits) | set(per_source_misses))
    by_source = [
        _compute_metrics(
            src,
            per_source_hits.get(src, 0),
            per_source_misses.get(src, 0),
        )
        for src in all_sources
    ]

    tv_summary = _summarize_tv_pipeline(tradingview_pending_signals_path)
    verdict, notes = _derive_verdict(overall, by_source)

    return ProvenanceReport(
        generated_at=datetime.now(UTC).isoformat(),
        overall=overall,
        by_source=by_source,
        tradingview_pipeline=tv_summary,
        verdict=verdict,
        notes=notes,
    )


def write_provenance_report(report: ProvenanceReport, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "report_type": "tv4_quality_bar_provenance_split",
        "generated_at": report.generated_at,
        "overall": asdict(report.overall),
        "by_source": [asdict(m) for m in report.by_source],
        "tradingview_pipeline": asdict(report.tradingview_pipeline),
        "verdict": report.verdict,
        "notes": list(report.notes),
        "min_sample_for_judgment": MIN_SAMPLE_FOR_JUDGMENT,
    }
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return output_path
