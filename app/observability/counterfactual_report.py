"""Read-only roll-up over the counterfactual Live/Replay drift evidence.

The dual-stream logger (:mod:`app.observability.counterfactual_replay_logger`)
appends one record per shadow candidate to
``artifacts/counterfactual_comparison.jsonl``, comparing the live entry price
against the settled Binance 1m kline. This module aggregates that append-only
stream into an operator-auditable summary: how many comparisons drifted beyond
threshold, how many are data-quality-suspect (a glitch, never counted as drift),
how many the entry-priceability gate *would* have rejected, plus the drift
distribution and per-symbol / per-source breakdowns.

READ-ONLY diagnostics — touches no runtime, no execution state, no env. Mirrors
the build/render/to_dict shape of :mod:`app.alerts.blocked_outcome_report` and
reads via the shared fail-soft :func:`app.storage.jsonl_io.read_jsonl_tolerant`.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.observability.counterfactual_replay_logger import OUTPUT_PATH
from app.storage.jsonl_io import read_jsonl_tolerant

_PCTILES = (50, 90, 99)


def _percentile(sorted_vals: list[float], q: float) -> float:
    """Nearest-rank percentile over a pre-sorted list (empty → 0.0)."""
    if not sorted_vals:
        return 0.0
    rank = max(1, math.ceil(q / 100.0 * len(sorted_vals)))
    return sorted_vals[min(rank, len(sorted_vals)) - 1]


def _num(value: object) -> float | None:
    """Coerce a JSON numeric (rejecting bool) to float, else None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _breakdown(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, int]] = defaultdict(
        lambda: {"total": 0, "drift_exceeded": 0, "data_quality_suspect": 0}
    )
    for row in rows:
        raw = row.get(key)
        name = raw.strip() if isinstance(raw, str) and raw.strip() else "unknown"
        bucket = buckets[name]
        bucket["total"] += 1
        if row.get("drift_exceeded") is True:
            bucket["drift_exceeded"] += 1
        if row.get("data_quality_suspect") is True:
            bucket["data_quality_suspect"] += 1
    return [{key: name, **counts} for name, counts in sorted(buckets.items())]


@dataclass
class CounterfactualReport:
    """Aggregated, operator-auditable view of the Live/Replay drift stream."""

    path: str
    total: int
    in_settled_range: int
    drift_exceeded: int
    data_quality_suspect: int
    gate_would_reject: int
    gate_unknown: int
    drift_abs_bps: dict[str, float]
    by_symbol: list[dict[str, Any]]
    by_source: list[dict[str, Any]]

    @property
    def available(self) -> bool:
        return self.total > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "total": self.total,
            "in_settled_range": self.in_settled_range,
            "drift_exceeded": self.drift_exceeded,
            "data_quality_suspect": self.data_quality_suspect,
            "gate_would_reject": self.gate_would_reject,
            "gate_unknown": self.gate_unknown,
            "drift_abs_bps": self.drift_abs_bps,
            "by_symbol": self.by_symbol,
            "by_source": self.by_source,
        }


def build_counterfactual_report(path: str | Path = OUTPUT_PATH) -> CounterfactualReport:
    """Build a read-only roll-up from the counterfactual comparison JSONL."""
    resolved = Path(path)
    rows = read_jsonl_tolerant(resolved)

    drift_exceeded = sum(1 for r in rows if r.get("drift_exceeded") is True)
    suspect = sum(1 for r in rows if r.get("data_quality_suspect") is True)
    in_range = sum(1 for r in rows if r.get("in_settled_range") is True)
    gate_reject = sum(1 for r in rows if r.get("gate_would_reject") is True)
    gate_unknown = sum(1 for r in rows if r.get("gate_would_reject") is None)

    # Drift distribution over NON-suspect records only: a data-quality-suspect
    # record is a glitch (>suspect_range_bps outside the settled range), never a
    # real drift, so it must not inflate the percentiles.
    drift_abs = sorted(
        abs(v)
        for r in rows
        if r.get("data_quality_suspect") is not True
        and (v := _num(r.get("drift_to_range_bps"))) is not None
    )
    pctiles: dict[str, float] = {f"p{q}": round(_percentile(drift_abs, q), 4) for q in _PCTILES}
    pctiles["max"] = round(drift_abs[-1], 4) if drift_abs else 0.0

    return CounterfactualReport(
        path=str(resolved),
        total=len(rows),
        in_settled_range=in_range,
        drift_exceeded=drift_exceeded,
        data_quality_suspect=suspect,
        gate_would_reject=gate_reject,
        gate_unknown=gate_unknown,
        drift_abs_bps=pctiles,
        by_symbol=_breakdown(rows, "symbol"),
        by_source=_breakdown(rows, "source"),
    )


def render_counterfactual_report(report: CounterfactualReport) -> str:
    """Render a compact operator report for the Live/Replay drift stream."""
    drift = report.drift_abs_bps
    lines = [
        "COUNTERFACTUAL LIVE-vs-REPLAY DRIFT REPORT",
        f"path: {report.path}",
        f"total_comparisons: {report.total}",
        f"in_settled_range: {report.in_settled_range}",
        f"drift_exceeded: {report.drift_exceeded}",
        f"data_quality_suspect: {report.data_quality_suspect}",
        f"gate_would_reject: {report.gate_would_reject} (unknown={report.gate_unknown})",
        f"drift_abs_bps: p50={drift.get('p50')} p90={drift.get('p90')} "
        f"p99={drift.get('p99')} max={drift.get('max')}",
        "",
    ]
    for title, key, rows in (
        ("BY SYMBOL", "symbol", report.by_symbol),
        ("BY SOURCE", "source", report.by_source),
    ):
        lines.append(title)
        if not rows:
            lines.append("  (none)")
        for row in rows:
            lines.append(
                f"  {row[key]}: total={row['total']} drift_exceeded={row['drift_exceeded']} "
                f"suspect={row['data_quality_suspect']}"
            )
        lines.append("")
    return "\n".join(lines).rstrip()


__all__ = [
    "CounterfactualReport",
    "build_counterfactual_report",
    "render_counterfactual_report",
]
