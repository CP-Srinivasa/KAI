"""Edge-Verlauf (UI-2026.06 Backlog #319): Precision / Brier / IC über die Zeit.

Bucketet den SHADOW-resolved-Ledger (``artifacts/shadow_candidate_resolved.jsonl``)
nach ``resolved_at_utc`` in zurückliegende Fenster und berechnet je Fenster die
Edge-Kennzahlen — damit das KI-Insights-Frontend Trend-/Kalibrierungs-Charts
zeigen kann, statt nur Momentwerte.

EHRLICH (wie der Edge-Collector): exakt dieselbe Outcome-/Real-Source-Logik
(``_resolve_outcome`` / ``REAL_SOURCES``) — kein zweiter Wahrheitsbegriff.
Fenster unter ``min_resolved`` liefern ``None`` (kein Chart-Punkt auf dünner
Stichprobe — keine erfundenen, irreführenden Trendlinien). Canary-Proben raus.

Reine Funktion (rows + ``now`` → Serie); IO nur im dünnen ``load_*``-Wrapper.
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.observability.generator_edge_collector import (
    DEFAULT_RESOLVED_PATH,
    REAL_SOURCES,
    _resolve_outcome,
)

# 1h-Forward-Return-Feld (deckungsgleich mit generator_edge_collector.HORIZON_FIELDS).
_ONE_H_FIELD = "fwd_3600s_bps"

DEFAULT_BUCKET_DAYS = 7
DEFAULT_NUM_BUCKETS = 6
DEFAULT_MIN_RESOLVED = 10


@dataclass(frozen=True)
class EdgeWindow:
    window_start: str  # ISO-UTC (inkl.)
    window_end: str  # ISO-UTC (exkl.)
    resolved: int
    precision_pct: float | None
    brier: float | None
    ic_1h: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return None
    return cov / math.sqrt(vx * vy)


def _brier(pairs: Sequence[tuple[float, int]]) -> float | None:
    if not pairs:
        return None
    return sum((p - o) ** 2 for p, o in pairs) / len(pairs)


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def build_edge_timeseries(
    rows: Sequence[dict[str, Any]],
    *,
    now: datetime,
    bucket_days: int = DEFAULT_BUCKET_DAYS,
    num_buckets: int = DEFAULT_NUM_BUCKETS,
    min_resolved: int = DEFAULT_MIN_RESOLVED,
    real_sources: tuple[str, ...] = REAL_SOURCES,
) -> list[EdgeWindow]:
    """Pure: resolved-rows → chronologische Edge-Fenster. ``now`` injizierbar (testbar)."""
    if bucket_days <= 0 or num_buckets <= 0:
        return []
    span = timedelta(days=bucket_days)
    # Fenster-Grenzen: [now - num*span, now], aufsteigend.
    edges = [now - span * (num_buckets - i) for i in range(num_buckets + 1)]

    # Pro Bucket Akkumulatoren.
    confs: list[list[float]] = [[] for _ in range(num_buckets)]
    fwds: list[list[float]] = [[] for _ in range(num_buckets)]
    pairs: list[list[tuple[float, int]]] = [[] for _ in range(num_buckets)]
    hits = [0] * num_buckets
    decided = [0] * num_buckets

    for row in rows:
        if not isinstance(row, dict) or row.get("is_canary"):
            continue
        if str(row.get("source")) not in real_sources:
            continue
        score = row.get("signal_confidence")
        if not isinstance(score, (int, float)):
            continue
        ts = _parse_ts(row.get("resolved_at_utc"))
        if ts is None or ts < edges[0] or ts >= edges[-1]:
            continue
        # Bucket-Index per Offset (gleich breite Fenster).
        idx = int((ts - edges[0]) / span)
        if idx < 0 or idx >= num_buckets:
            continue
        score_f = float(score)
        fwd = row.get(_ONE_H_FIELD)
        if isinstance(fwd, (int, float)):
            confs[idx].append(score_f)
            fwds[idx].append(float(fwd))
        outcome = _resolve_outcome(row)
        if outcome is not None:
            pairs[idx].append((score_f, outcome))
            decided[idx] += 1
            hits[idx] += outcome

    out: list[EdgeWindow] = []
    for i in range(num_buckets):
        enough = decided[i] >= min_resolved
        precision = (100.0 * hits[i] / decided[i]) if (enough and decided[i] > 0) else None
        brier = _brier(pairs[i]) if enough else None
        ic = _pearson(confs[i], fwds[i]) if enough else None
        out.append(
            EdgeWindow(
                window_start=edges[i].isoformat(),
                window_end=edges[i + 1].isoformat(),
                resolved=decided[i],
                precision_pct=round(precision, 2) if precision is not None else None,
                brier=round(brier, 6) if brier is not None else None,
                ic_1h=round(ic, 4) if ic is not None else None,
            )
        )
    return out


def load_edge_timeseries(
    resolved_path: Path = DEFAULT_RESOLVED_PATH,
    *,
    now: datetime | None = None,
    bucket_days: int = DEFAULT_BUCKET_DAYS,
    num_buckets: int = DEFAULT_NUM_BUCKETS,
    min_resolved: int = DEFAULT_MIN_RESOLVED,
) -> list[EdgeWindow]:
    """IO-Wrapper: liest den resolved-Ledger und baut die Serie. Fehlende/kaputte
    Datei → leere Serie (honest leer, nie erfunden)."""
    rows: list[dict[str, Any]] = []
    if resolved_path.exists():
        try:
            with resolved_path.open("r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except (ValueError, TypeError):
                        continue
                    if isinstance(row, dict):
                        rows.append(row)
        except OSError:
            return []
    return build_edge_timeseries(
        rows,
        now=now or datetime.now(UTC),
        bucket_days=bucket_days,
        num_buckets=num_buckets,
        min_resolved=min_resolved,
    )
