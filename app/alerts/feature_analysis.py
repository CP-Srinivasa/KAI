"""Feature-level breakdown of resolved directional alert outcomes (D-141).

Aggregates hit/miss counts per bucket (asset, sentiment, priority, source) from
``alert_audit.jsonl`` + ``alert_outcomes.jsonl`` so the operator can see *where*
the 58% false-positive rate sits. Non-signal-critical analysis tooling — does
not touch signal generation, thresholds or eligibility logic.

Design mirrors ``app/alerts/hold_metrics.py`` (pure function over loaded
audit/annotation records) to keep the CLI wrapper thin and the logic unit
testable without DB or filesystem.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.alerts.audit import AlertAuditRecord, AlertOutcomeAnnotation
from app.alerts.eligibility import evaluate_directional_eligibility

_DIRECTIONAL_SENTIMENTS = frozenset({"bullish", "bearish"})


@dataclass(frozen=True)
class FeatureBucket:
    label: str
    resolved: int
    hits: int
    miss: int
    precision_pct: float | None

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "resolved": self.resolved,
            "hits": self.hits,
            "miss": self.miss,
            "precision_pct": self.precision_pct,
        }


def _rate_pct(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator * 100.0, 2)


def _latest_directional_by_doc(
    audits: list[AlertAuditRecord],
) -> dict[str, AlertAuditRecord]:
    """Mirror the directional dedup logic used by hold_metrics / pending_annotations.

    Keeps only non-digest records whose sentiment is bullish/bearish and which
    pass directional eligibility (either explicitly via ``directional_eligible``
    or, for legacy rows without that field, via a recomputed check). Last
    dispatch wins on ``document_id``.
    """
    latest: dict[str, AlertAuditRecord] = {}
    for rec in audits:
        if rec.is_digest:
            continue
        sentiment = (rec.sentiment_label or "").lower()
        if sentiment not in _DIRECTIONAL_SENTIMENTS:
            continue
        if rec.directional_eligible is False:
            continue
        if rec.directional_eligible is None:
            legacy = evaluate_directional_eligibility(
                sentiment_label=rec.sentiment_label,
                affected_assets=list(rec.affected_assets or []),
            )
            if legacy.directional_eligible is not True:
                continue
        prev = latest.get(rec.document_id)
        if prev is None or rec.dispatched_at > prev.dispatched_at:
            latest[rec.document_id] = rec
    return latest


def _build_buckets(
    doc_to_labels: dict[str, list[str]],
    hit_docs: set[str],
    miss_docs: set[str],
    min_bucket_size: int,
) -> list[FeatureBucket]:
    """Aggregate hit/miss per label across docs.

    A document can contribute to multiple buckets (e.g. an alert affecting
    both BTC and ETH counts toward both asset buckets). Bucket totals can
    therefore sum to more than the number of unique resolved documents — this
    is intentional: the question each bucket answers is conditional
    ("when label X was present, how often was the direction correct?").
    """
    hits_per_label: Counter[str] = Counter()
    miss_per_label: Counter[str] = Counter()
    for doc_id, labels in doc_to_labels.items():
        seen: set[str] = set()
        for label in labels:
            if not label or label in seen:
                continue
            seen.add(label)
            if doc_id in hit_docs:
                hits_per_label[label] += 1
            elif doc_id in miss_docs:
                miss_per_label[label] += 1
    labels = set(hits_per_label) | set(miss_per_label)
    buckets: list[FeatureBucket] = []
    for label in labels:
        hits = hits_per_label.get(label, 0)
        miss = miss_per_label.get(label, 0)
        resolved = hits + miss
        if resolved < min_bucket_size:
            continue
        buckets.append(
            FeatureBucket(
                label=label,
                resolved=resolved,
                hits=hits,
                miss=miss,
                precision_pct=_rate_pct(hits, resolved),
            )
        )
    buckets.sort(key=lambda b: (-b.resolved, b.label))
    return buckets


def _forward_eligible(
    rec: AlertAuditRecord,
    source_name: str | None = None,
    title: str | None = None,
) -> bool:
    """Re-evaluate a resolved alert through ALL current eligibility gates.

    Uses only fields available in the audit record (no scores/confidence).
    Returns True if the alert would still be directional-eligible under
    today's rules.
    """
    check = evaluate_directional_eligibility(
        sentiment_label=rec.sentiment_label,
        affected_assets=list(rec.affected_assets or []),
        priority=rec.priority,
        actionable=rec.actionable,
        source_name=source_name,
        title=title,
    )
    return check.directional_eligible is True


def build_feature_analysis(
    audits: list[AlertAuditRecord],
    annotations: list[AlertOutcomeAnnotation],
    source_by_doc: dict[str, str] | None = None,
    title_by_doc: dict[str, str] | None = None,
    min_bucket_size: int = 3,
) -> dict[str, Any]:
    """Compute bucketed hit/miss/precision over resolved directional alerts.

    Parameters
    ----------
    audits:
        All alert audit records (typically loaded from ``alert_audit.jsonl``).
    annotations:
        All operator outcome annotations. Last annotation per document wins.
    source_by_doc:
        Optional ``document_id -> source_name`` map for the ``by_source``
        bucket. Omit to skip the source breakdown entirely (e.g. in unit
        tests or when no DB is available).
    title_by_doc:
        Optional ``document_id -> title`` map for the reactive-narrative
        gate in forward simulation. Falls back to audit record
        ``normalized_title`` when omitted or missing for a doc.
    min_bucket_size:
        Minimum resolved-count a label must reach to show up in a bucket.
        Default 3 to suppress single-observation noise.
    """
    latest_ann_by_doc: dict[str, str] = {}
    for ann in annotations:
        latest_ann_by_doc[ann.document_id] = ann.outcome

    latest_directional = _latest_directional_by_doc(audits)
    directional_doc_ids = set(latest_directional.keys())

    hit_docs = {d for d in directional_doc_ids if latest_ann_by_doc.get(d) == "hit"}
    miss_docs = {d for d in directional_doc_ids if latest_ann_by_doc.get(d) == "miss"}
    inconclusive_docs = {
        d for d in directional_doc_ids if latest_ann_by_doc.get(d) == "inconclusive"
    }
    resolved_docs = hit_docs | miss_docs

    # Label maps per bucket dimension
    assets_by_doc: dict[str, list[str]] = {}
    sentiment_by_doc: dict[str, list[str]] = {}
    priority_by_doc: dict[str, list[str]] = {}
    priority_group_by_doc: dict[str, list[str]] = {}
    for doc_id, rec in latest_directional.items():
        assets_by_doc[doc_id] = [a.strip() for a in (rec.affected_assets or []) if a]
        sentiment_by_doc[doc_id] = [(rec.sentiment_label or "").lower() or "unknown"]
        if rec.priority is None:
            priority_by_doc[doc_id] = ["unknown"]
            priority_group_by_doc[doc_id] = ["unknown"]
        else:
            priority_by_doc[doc_id] = [f"p{rec.priority}"]
            priority_group_by_doc[doc_id] = (
                ["high (>=7)"] if rec.priority >= 7 else ["low (<7)"]
            )

    by_asset = _build_buckets(assets_by_doc, hit_docs, miss_docs, min_bucket_size)
    by_sentiment = _build_buckets(
        sentiment_by_doc, hit_docs, miss_docs, min_bucket_size
    )
    by_priority = _build_buckets(
        priority_by_doc, hit_docs, miss_docs, min_bucket_size
    )
    by_priority_group = _build_buckets(
        priority_group_by_doc, hit_docs, miss_docs, min_bucket_size
    )

    by_source: list[FeatureBucket] | None = None
    if source_by_doc is not None:
        source_map: dict[str, list[str]] = {}
        for doc_id in latest_directional:
            source_map[doc_id] = [source_by_doc.get(doc_id) or "unknown"]
        by_source = _build_buckets(
            source_map, hit_docs, miss_docs, min_bucket_size
        )

    precision_overall = _rate_pct(len(hit_docs), len(resolved_docs))

    # Forward simulation: re-evaluate resolved alerts through current gates
    def _fwd_title(doc_id: str) -> str | None:
        rec = latest_directional[doc_id]
        return rec.normalized_title or (title_by_doc or {}).get(doc_id)

    fwd_hits = {
        d for d in hit_docs
        if _forward_eligible(
            latest_directional[d],
            (source_by_doc or {}).get(d),
            _fwd_title(d),
        )
    }
    fwd_misses = {
        d for d in miss_docs
        if _forward_eligible(
            latest_directional[d],
            (source_by_doc or {}).get(d),
            _fwd_title(d),
        )
    }
    fwd_resolved = len(fwd_hits) + len(fwd_misses)
    fwd_filtered_out = len(resolved_docs) - fwd_resolved

    report: dict[str, Any] = {
        "report_type": "ph5_feature_analysis",
        "generated_at": datetime.now(UTC).isoformat(),
        "min_bucket_size": min_bucket_size,
        "totals": {
            "directional_alerts": len(directional_doc_ids),
            "hits": len(hit_docs),
            "miss": len(miss_docs),
            "resolved": len(resolved_docs),
            "inconclusive": len(inconclusive_docs),
            "unlabeled": len(directional_doc_ids - resolved_docs - inconclusive_docs),
            "precision_pct": precision_overall,
        },
        "forward_simulation": {
            "hits": len(fwd_hits),
            "miss": len(fwd_misses),
            "resolved": fwd_resolved,
            "filtered_out": fwd_filtered_out,
            "precision_pct": _rate_pct(len(fwd_hits), fwd_resolved),
        },
        "buckets": {
            "by_sentiment": [b.to_json_dict() for b in by_sentiment],
            "by_priority": [b.to_json_dict() for b in by_priority],
            "by_priority_group": [b.to_json_dict() for b in by_priority_group],
            "by_asset": [b.to_json_dict() for b in by_asset],
        },
    }
    if by_source is not None:
        report["buckets"]["by_source"] = [b.to_json_dict() for b in by_source]
    return report
