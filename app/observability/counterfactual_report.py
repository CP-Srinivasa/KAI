"""Read-only roll-up over the counterfactual Live/Replay drift evidence.

The dual-stream logger (:mod:`app.observability.counterfactual_replay_logger`)
appends one record per shadow candidate to
``artifacts/counterfactual_comparison.jsonl``, comparing the live entry price
against the settled Binance 1m kline. This module aggregates that append-only
stream into an operator-auditable summary: how many comparisons drifted beyond
threshold, how many are data-quality-suspect (a glitch, never counted as drift),
how many the entry-priceability gate *would* have rejected, plus the drift
distribution and per-symbol / per-source breakdowns (each carrying a SIGNED
drift bias that exposes a systematic venue skew the |abs| percentiles hide).

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

from app.observability.counterfactual_replay_logger import OUTPUT_PATH, SUSPECT_RANGE_BPS
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


def _is_suspect(row: dict[str, Any]) -> bool:
    """Read-time plausibility: a record is a data-quality glitch if the logger
    flagged it OR if its out-of-range drift exceeds the physical suspect ceiling.

    The second clause is what makes this report robust to the pre-v2 backlog:
    v1 records were logged before the suspect gate existed, so an impossible
    ~100-index-vs-sub-$1 drift (millions of bps) carries ``drift_exceeded=True``
    and no ``data_quality_suspect`` field. Re-deriving the flag here keeps such
    glitches out of the drift count and the percentile distribution regardless
    of the schema version the record was written under.
    """
    if row.get("data_quality_suspect") is True:
        return True
    drift = _num(row.get("drift_to_range_bps"))
    return drift is not None and abs(drift) > SUSPECT_RANGE_BPS


def _breakdown(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    counts: dict[str, dict[str, int]] = defaultdict(
        lambda: {"total": 0, "drift_exceeded": 0, "data_quality_suspect": 0}
    )
    # SIGNED drift per bucket (non-suspect, numeric only): the mean of the signed
    # drift_to_range_bps reveals a SYSTEMATIC venue bias — a source that
    # consistently prices above (+) or below (-) the settled range — which the
    # |abs| percentiles alone cannot show (a symmetric ±X spread and a one-sided
    # +X bias have the same p50).
    signed: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        raw = row.get(key)
        name = raw.strip() if isinstance(raw, str) and raw.strip() else "unknown"
        bucket = counts[name]
        bucket["total"] += 1
        suspect = _is_suspect(row)
        if row.get("drift_exceeded") is True and not suspect:
            bucket["drift_exceeded"] += 1
        if suspect:
            bucket["data_quality_suspect"] += 1
        else:
            drift = _num(row.get("drift_to_range_bps"))
            if drift is not None:
                signed[name].append(drift)
    out: list[dict[str, Any]] = []
    for name in sorted(counts):
        vals = signed[name]
        bias = round(sum(vals) / len(vals), 4) if vals else 0.0
        out.append({key: name, **counts[name], "signed_drift_bps": bias})
    return out


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

    suspect = sum(1 for r in rows if _is_suspect(r))
    # A read-time-suspect glitch is never a real drift, even if the stored
    # ``drift_exceeded`` (v1 backlog) says otherwise.
    drift_exceeded = sum(1 for r in rows if r.get("drift_exceeded") is True and not _is_suspect(r))
    in_range = sum(1 for r in rows if r.get("in_settled_range") is True)
    gate_reject = sum(1 for r in rows if r.get("gate_would_reject") is True)
    gate_unknown = sum(1 for r in rows if r.get("gate_would_reject") is None)

    # Drift distribution over NON-suspect records only: a data-quality-suspect
    # record is a glitch (>suspect_range_bps outside the settled range), never a
    # real drift, so it must not inflate the percentiles.
    drift_abs = sorted(
        abs(v)
        for r in rows
        if not _is_suspect(r) and (v := _num(r.get("drift_to_range_bps"))) is not None
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
                f"suspect={row['data_quality_suspect']} "
                f"signed_bias={row['signed_drift_bps']:+.1f}bps"
            )
        lines.append("")
    return "\n".join(lines).rstrip()


__all__ = [
    "CounterfactualReport",
    "build_counterfactual_report",
    "render_counterfactual_report",
]
