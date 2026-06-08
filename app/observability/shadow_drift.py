"""Shadow ledger drift checks.

Read-only health logic for the Phase-B shadow learning stream. It flags the two
failure modes that are easy to miss in manual reviews: the ledger stopped
growing, or the generator keeps writing degenerate feature values.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.observability.shadow_candidate_ledger import LEDGER_PATH
from app.storage.jsonl_io import read_jsonl_tolerant

STATUS_OK = "ok"
STATUS_WARN = "warn"


@dataclass(frozen=True)
class FeatureVariance:
    field: str
    sample_count: int
    variance: float | None
    is_degenerate: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "sample_count": self.sample_count,
            "variance": self.variance,
            "is_degenerate": self.is_degenerate,
        }


@dataclass(frozen=True)
class ShadowDriftReport:
    generated_at: str
    ledger_path: str
    window_hours: float
    min_rows: int
    total_rows: int
    rows_in_window: int
    latest_ts_utc: str | None
    status: str
    reasons: list[str] = field(default_factory=list)
    feature_variance: list[FeatureVariance] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "ledger_path": self.ledger_path,
            "window_hours": self.window_hours,
            "min_rows": self.min_rows,
            "total_rows": self.total_rows,
            "rows_in_window": self.rows_in_window,
            "latest_ts_utc": self.latest_ts_utc,
            "status": self.status,
            "reasons": self.reasons,
            "feature_variance": [v.to_dict() for v in self.feature_variance],
        }


def _parse_ts(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _as_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _feature_variance(
    rows: list[dict[str, Any]],
    *,
    fields: tuple[str, ...],
    min_samples: int,
    epsilon: float,
) -> list[FeatureVariance]:
    out: list[FeatureVariance] = []
    for field_name in fields:
        vals = [num for row in rows if (num := _as_float(row.get(field_name))) is not None]
        var = statistics.pvariance(vals) if len(vals) >= 2 else None
        out.append(
            FeatureVariance(
                field=field_name,
                sample_count=len(vals),
                variance=None if var is None else round(var, 12),
                is_degenerate=len(vals) >= min_samples and var is not None and var <= epsilon,
            )
        )
    return out


def build_shadow_drift_report(
    *,
    ledger_path: Path = LEDGER_PATH,
    now: datetime | None = None,
    window_hours: float = 24.0,
    min_rows: int = 1,
    min_variance_samples: int = 5,
    variance_epsilon: float = 1e-9,
    feature_fields: tuple[str, ...] = ("signal_confidence", "recommended_priority", "rr"),
) -> ShadowDriftReport:
    """Build a read-only ledger-health report.

    A warning means "do not trust the shadow learning stream until inspected";
    it never changes execution state and never writes to the ledger.
    """
    now_utc = now or datetime.now(UTC)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=UTC)
    cutoff = now_utc - timedelta(hours=window_hours)

    rows = read_jsonl_tolerant(ledger_path)
    rows_with_ts: list[tuple[dict[str, Any], datetime]] = []
    for row in rows:
        ts = _parse_ts(row.get("ts_utc"))
        if ts is not None:
            rows_with_ts.append((row, ts))

    latest = max((ts for _row, ts in rows_with_ts), default=None)
    window_rows = [row for row, ts in rows_with_ts if ts >= cutoff]
    variances = _feature_variance(
        window_rows,
        fields=feature_fields,
        min_samples=min_variance_samples,
        epsilon=variance_epsilon,
    )

    reasons: list[str] = []
    if not ledger_path.exists():
        reasons.append("missing_ledger")
    if len(window_rows) < min_rows:
        reasons.append("ledger_growth_below_min")
    for var in variances:
        if var.is_degenerate:
            reasons.append(f"feature_degenerate:{var.field}")

    return ShadowDriftReport(
        generated_at=now_utc.isoformat(),
        ledger_path=str(ledger_path),
        window_hours=window_hours,
        min_rows=min_rows,
        total_rows=len(rows),
        rows_in_window=len(window_rows),
        latest_ts_utc=None if latest is None else latest.isoformat(),
        status=STATUS_WARN if reasons else STATUS_OK,
        reasons=reasons,
        feature_variance=variances,
    )


__all__ = [
    "STATUS_OK",
    "STATUS_WARN",
    "FeatureVariance",
    "ShadowDriftReport",
    "build_shadow_drift_report",
]
