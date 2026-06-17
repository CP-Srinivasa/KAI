"""Tests für den Edge-Verlauf (#319) — pure build_edge_timeseries."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.observability.edge_timeseries import EdgeWindow, build_edge_timeseries

NOW = datetime(2026, 6, 17, 12, 0, 0, tzinfo=UTC)


def _row(
    days_ago: float,
    conf: float,
    *,
    fwd: float | None,
    outcome_fwd: float | None,
    source: str = "autonomous_generator",
    canary: bool = False,
) -> dict:
    ts = (NOW - timedelta(days=days_ago)).isoformat()
    row: dict = {
        "resolved_at_utc": ts,
        "signal_confidence": conf,
        "source": source,
        "is_canary": canary,
    }
    if fwd is not None:
        row["fwd_3600s_bps"] = fwd
    # Outcome wird über fwd_60s_bps-Vorzeichen entschieden (Fallback in _resolve_outcome);
    # wir setzen es explizit für deterministische Hits.
    if outcome_fwd is not None:
        row["fwd_60s_bps"] = outcome_fwd
    return row


def test_buckets_by_time_and_gates_min_resolved():
    # 12 Rows im jüngsten 7d-Fenster (alle entscheidbar) → über min_resolved=10.
    # conf variiert + fwd korreliert → IC ist definiert (nicht-konstantes Feature).
    rows = [
        _row(1.0 + i * 0.1, 0.5 + i * 0.02, fwd=float(i + 1), outcome_fwd=5.0) for i in range(12)
    ]
    series = build_edge_timeseries(rows, now=NOW, bucket_days=7, num_buckets=2, min_resolved=10)
    assert len(series) == 2
    recent = series[-1]
    assert recent.resolved == 12
    assert recent.precision_pct == 100.0  # alle outcome_fwd>0 → hit
    assert recent.brier is not None and recent.ic_1h is not None


def test_below_min_resolved_is_none_not_invented():
    rows = [_row(1.0, 0.5, fwd=1.0, outcome_fwd=1.0) for _ in range(3)]  # nur 3 < 10
    series = build_edge_timeseries(rows, now=NOW, bucket_days=7, num_buckets=1, min_resolved=10)
    assert series[-1].resolved == 3
    assert series[-1].precision_pct is None
    assert series[-1].brier is None
    assert series[-1].ic_1h is None


def test_excludes_canary_and_non_real_sources():
    rows = [_row(1.0, 0.6, fwd=2.0, outcome_fwd=2.0, canary=True) for _ in range(20)] + [
        _row(1.0, 0.6, fwd=2.0, outcome_fwd=2.0, source="tv_promoted") for _ in range(20)
    ]
    series = build_edge_timeseries(rows, now=NOW, bucket_days=7, num_buckets=1, min_resolved=1)
    assert series[-1].resolved == 0  # nichts zählt


def test_brier_value_and_precision_split():
    # 10 Rows conf=0.5: 5 Treffer (outcome 1), 5 Miss (outcome 0). Brier = 0.25.
    rows = [_row(1.0, 0.5, fwd=1.0, outcome_fwd=1.0) for _ in range(5)] + [
        _row(1.0, 0.5, fwd=-1.0, outcome_fwd=-1.0) for _ in range(5)
    ]
    series = build_edge_timeseries(rows, now=NOW, bucket_days=7, num_buckets=1, min_resolved=10)
    w = series[-1]
    assert w.resolved == 10
    assert w.precision_pct == 50.0
    assert w.brier == 0.25


def test_empty_and_bad_config():
    # Leere Rows → Fenster-Skelett (honest resolved=0), NICHT []: die Chart-Achse
    # bleibt vollständig, Punkte sind nur leer.
    empty = build_edge_timeseries([], now=NOW, num_buckets=6)
    assert len(empty) == 6
    assert all(
        isinstance(w, EdgeWindow) and w.resolved == 0 and w.precision_pct is None for w in empty
    )
    # Kaputte Konfig (num_buckets/bucket_days <= 0) → [].
    assert build_edge_timeseries([_row(1, 0.5, fwd=1, outcome_fwd=1)], now=NOW, num_buckets=0) == []
