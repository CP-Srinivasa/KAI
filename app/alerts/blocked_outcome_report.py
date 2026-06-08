"""Read-only D-227 blocked-outcome report.

The blocked-alert outcome stream is append-only, so duplicate document IDs are
expected when stale inconclusive annotations are re-evaluated. This report keeps
both views visible: raw event volume for idempotency checks and latest-by-doc
outcomes for hit/miss analysis.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.alerts.blocked_audit import BLOCKED_OUTCOMES_JSONL_FILENAME
from app.storage.jsonl_io import read_jsonl_tolerant

_OUTCOMES = {"hit", "miss", "inconclusive"}


def _parse_iso(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _resolve_outcomes_path(path: str | Path) -> Path:
    p = Path(path)
    if p.is_dir() or p.suffix == "":
        return p / BLOCKED_OUTCOMES_JSONL_FILENAME
    return p


def _latest_by_document_id(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    latest: dict[str, tuple[datetime | None, int, dict[str, Any]]] = {}
    for idx, row in enumerate(rows):
        doc_id = row.get("document_id")
        if not isinstance(doc_id, str) or not doc_id.strip():
            continue
        doc_id = doc_id.strip()
        ts = _parse_iso(row.get("annotated_at"))
        prior = latest.get(doc_id)
        current_key = (ts or datetime.min.replace(tzinfo=UTC), idx)
        if prior is None or current_key >= (
            prior[0] or datetime.min.replace(tzinfo=UTC),
            prior[1],
        ):
            latest[doc_id] = (ts, idx, row)
    return {doc_id: row for doc_id, (_ts, _idx, row) in sorted(latest.items())}


def _outcome_bucket(row: dict[str, Any]) -> str:
    outcome = row.get("outcome")
    return outcome if isinstance(outcome, str) and outcome in _OUTCOMES else "inconclusive"


def _value(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        val = row.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return "unknown"


def _confidence_bucket(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "unknown"
    confidence = float(value)
    if confidence < 0.0 or confidence > 1.0:
        return "unknown"
    if confidence >= 1.0:
        return "1.0"
    lo = int(confidence * 10) / 10
    hi = min(lo + 0.1, 1.0)
    return f"{lo:.1f}-{hi:.1f}"


def _hit_miss_summary(
    rows: list[dict[str, Any]],
    *,
    key_name: str,
    bucket_for: Any,
) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, int]] = defaultdict(
        lambda: {"hit": 0, "miss": 0, "inconclusive": 0}
    )
    for row in rows:
        bucket = str(bucket_for(row))
        buckets[bucket][_outcome_bucket(row)] += 1

    out: list[dict[str, Any]] = []
    for bucket, counts in sorted(buckets.items()):
        resolved = counts["hit"] + counts["miss"]
        precision = None if resolved == 0 else round(counts["hit"] / resolved * 100.0, 2)
        out.append(
            {
                key_name: bucket,
                "hit": counts["hit"],
                "miss": counts["miss"],
                "inconclusive": counts["inconclusive"],
                "resolved": resolved,
                "precision_pct": precision,
            }
        )
    return out


def build_blocked_outcome_report(path: str | Path = "artifacts") -> dict[str, Any]:
    """Build a read-only D-227 outcome report from blocked_outcomes.jsonl."""
    outcomes_path = _resolve_outcomes_path(path)
    raw_rows = read_jsonl_tolerant(outcomes_path)
    latest_by_doc = _latest_by_document_id(raw_rows)
    latest_rows = list(latest_by_doc.values())
    reevaluation_count = sum(
        1
        for row in raw_rows
        if isinstance(row.get("note"), str) and row["note"].startswith("reeval[")
    )

    return {
        "outcomes_path": str(outcomes_path),
        "raw_events_count": len(raw_rows),
        "distinct_document_id_count": len(latest_by_doc),
        "reevaluation_count": reevaluation_count,
        "latest_outcome_by_document_id": latest_by_doc,
        "hit_miss_by_block_reason": _hit_miss_summary(
            latest_rows,
            key_name="block_reason",
            bucket_for=lambda row: _value(row, "block_reason"),
        ),
        "hit_miss_by_source": _hit_miss_summary(
            latest_rows,
            key_name="source",
            bucket_for=lambda row: _value(row, "source_name", "source"),
        ),
        "hit_miss_by_sentiment": _hit_miss_summary(
            latest_rows,
            key_name="sentiment",
            bucket_for=lambda row: _value(row, "sentiment_label"),
        ),
        "hit_miss_by_confidence": _hit_miss_summary(
            latest_rows,
            key_name="confidence_bucket",
            bucket_for=lambda row: _confidence_bucket(row.get("directional_confidence")),
        ),
    }


def render_blocked_outcome_report(report: dict[str, Any]) -> str:
    """Render a compact operator report for the D-227 blocked outcome stream."""
    lines = [
        "D-227 BLOCKED OUTCOME REPORT",
        f"path: {report['outcomes_path']}",
        f"raw_events_count: {report['raw_events_count']}",
        f"distinct_document_id_count: {report['distinct_document_id_count']}",
        f"reevaluation_count: {report['reevaluation_count']}",
        "",
    ]
    for title, key, label in (
        ("HIT/MISS BY BLOCK REASON", "hit_miss_by_block_reason", "block_reason"),
        ("HIT/MISS BY SOURCE", "hit_miss_by_source", "source"),
        ("HIT/MISS BY SENTIMENT", "hit_miss_by_sentiment", "sentiment"),
        ("HIT/MISS BY CONFIDENCE", "hit_miss_by_confidence", "confidence_bucket"),
    ):
        lines.append(title)
        rows = report.get(key, [])
        if not rows:
            lines.append("  (none)")
            lines.append("")
            continue
        for row in rows:
            lines.append(
                f"  {row[label]}: hit={row['hit']} miss={row['miss']} "
                f"inconclusive={row['inconclusive']} resolved={row['resolved']} "
                f"precision_pct={row['precision_pct']}"
            )
        lines.append("")
    return "\n".join(lines).rstrip()


__all__ = ["build_blocked_outcome_report", "render_blocked_outcome_report"]
