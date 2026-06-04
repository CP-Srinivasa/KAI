"""Tests for the formal metric registry / Truth-Layer v2 (NEO /goal Aufgabe 2).

Covers the five honesty requirements from the goal:
- the dashboard (frontend) is not permitted to compute a critical metric itself
- an outdated calculation_version raises a warning
- a metric without a registry entry is not served
- reconciliation tolerates only the declared deviations
- missing data yields a degraded status, never a fabricated value
"""

from __future__ import annotations

import pytest

from app.observability.metric_registry import (
    STATUS_DEGRADED,
    STATUS_OK,
    STATUS_STALE,
    STATUS_UNKNOWN,
    MetricComputation,
    MetricDefinition,
    MetricRegistry,
)


def _def(metric_id: str = "pnl_realized", **over: object) -> MetricDefinition:
    base: dict[str, object] = {
        "metric_id": metric_id,
        "name": "Realized PnL",
        "owner": "Neo",
        "calculation_version": "1.0.0",
        "source_tables": ("trades", "positions"),
        "calculation_function": "compute_realized_pnl",
        "tolerance_abs": 0.5,
        "tolerance_pct": 1.0,
        "frequency": "realtime",
        "staleness_limit_ms": 60_000,
        "display_allowed": True,
        "frontend_calculation_allowed": False,
    }
    base.update(over)
    return MetricDefinition(**base)  # type: ignore[arg-type]


def _reg_with(
    value: float | None, *, ts_ms: int | None = 1_000_000, **defover: object
) -> MetricRegistry:
    reg = MetricRegistry()
    d = _def(**defover)
    reg.register(
        d,
        lambda: MetricComputation(
            value=value,
            data_version="data-2026-06-05",
            source_timestamp_ms=ts_ms,
            confidence=0.9,
            methodology="sum(closed_trade_pnl)",
        ),
    )
    return reg


# 1) frontend must not compute critical metrics itself ─────────────────────────


def test_frontend_calculation_not_allowed_by_default():
    reg = _reg_with(42.0)
    assert reg.is_frontend_calculation_allowed("pnl_realized") is False


def test_frontend_calculation_unknown_metric_fails_closed():
    reg = MetricRegistry()
    assert reg.is_frontend_calculation_allowed("nope") is False


# 2) outdated calculation_version → warning ────────────────────────────────────


def test_outdated_calculation_version_emits_warning():
    reg = _reg_with(42.0)
    res = reg.serve("pnl_realized", now_ms=1_000_000, expected_calculation_version="0.9.0")
    assert res.status == STATUS_OK
    assert res.warning is not None
    assert "calculation_version mismatch" in res.warning
    assert res.calculation_version == "1.0.0"


def test_matching_calculation_version_no_warning():
    reg = _reg_with(42.0)
    res = reg.serve("pnl_realized", now_ms=1_000_000, expected_calculation_version="1.0.0")
    assert res.warning is None


# 3) metric without registry entry is not served ──────────────────────────────


def test_unknown_metric_is_refused():
    reg = MetricRegistry()
    res = reg.serve("ghost_metric", now_ms=1_000_000)
    assert res.status == STATUS_UNKNOWN
    assert res.value is None
    assert res.warning is not None and "not in the registry" in res.warning


def test_double_registration_rejected():
    reg = _reg_with(1.0)
    with pytest.raises(ValueError, match="already registered"):
        reg.register(_def(), lambda: MetricComputation(value=2.0, data_version="x"))


# 4) reconciliation tolerates only declared deviations ────────────────────────


def test_reconcile_within_abs_tolerance():
    reg = _reg_with(100.0)  # tolerance_abs=0.5, tolerance_pct=1.0
    r = reg.reconcile("pnl_realized", 100.4, now_ms=1_000_000)
    assert r.within_tolerance is True
    assert r.reason == "within_tolerance"


def test_reconcile_within_pct_tolerance_for_large_magnitude():
    reg = _reg_with(10_000.0)  # abs dev 50 > 0.5, but pct dev 0.5% <= 1.0%
    r = reg.reconcile("pnl_realized", 10_050.0, now_ms=1_000_000)
    assert r.within_tolerance is True
    assert r.deviation_pct is not None and r.deviation_pct <= 1.0


def test_reconcile_rejects_excess_deviation():
    reg = _reg_with(100.0)
    r = reg.reconcile("pnl_realized", 105.0, now_ms=1_000_000)  # 5 abs / 5% — both exceed
    assert r.within_tolerance is False
    assert r.reason == "deviation_exceeds_tolerance"
    assert r.deviation_abs == pytest.approx(5.0)


def test_reconcile_unknown_metric_never_ok():
    reg = MetricRegistry()
    r = reg.reconcile("ghost", 1.0, now_ms=1_000_000)
    assert r.within_tolerance is False
    assert r.reason == "unknown_metric"


def test_reconcile_degraded_ssot_never_ok():
    reg = _reg_with(None)  # no data
    r = reg.reconcile("pnl_realized", 1.0, now_ms=1_000_000)
    assert r.within_tolerance is False
    assert r.reason == "ssot_degraded"


# 5) missing data → degraded, no fantasy value ────────────────────────────────


def test_missing_data_is_degraded_not_zero():
    reg = _reg_with(None)
    res = reg.serve("pnl_realized", now_ms=1_000_000)
    assert res.status == STATUS_DEGRADED
    assert res.value is None  # NOT 0.0
    assert res.warning is not None and "value withheld" in res.warning


# staleness ────────────────────────────────────────────────────────────────────


def test_stale_source_data_flagged():
    # source ts far in the past relative to now → staleness > limit
    reg = _reg_with(42.0, ts_ms=1_000_000)
    res = reg.serve("pnl_realized", now_ms=1_000_000 + 120_000)  # 120s > 60s limit
    assert res.status == STATUS_STALE
    assert res.staleness_ms == 120_000
    assert res.warning is not None and "stale" in res.warning


def test_fresh_source_data_ok():
    reg = _reg_with(42.0, ts_ms=1_000_000)
    res = reg.serve("pnl_realized", now_ms=1_000_000 + 5_000)
    assert res.status == STATUS_OK
    assert res.value == 42.0
    assert res.staleness_ms == 5_000


def test_registry_introspection_serialises_definitions():
    reg = _reg_with(1.0)
    d = reg.to_dict()
    assert d["metric_count"] == 1
    assert "pnl_realized" in d["metrics"]
    assert d["metrics"]["pnl_realized"]["calculation_version"] == "1.0.0"
    assert d["metrics"]["pnl_realized"]["frontend_calculation_allowed"] is False
