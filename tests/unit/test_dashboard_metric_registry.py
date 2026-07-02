"""Dashboard metric registry wiring (Issue #170 Part A).

Pins the Truth-Layer v2 contract for the dashboard cohort:
- every dashboard metric forbids frontend computation (fail-closed guard)
- live-sourced metrics serve their value; unsourced ones serve ``degraded`` with
  no fabricated number
- the builder is pure: same input values → same served envelope
- reconciliation of a contract snapshot against the SSOT flags drift as a
  warning (within_tolerance False), never raising
"""

from __future__ import annotations

from app.observability.dashboard_metric_registry import (
    ALL_METRIC_IDS,
    DECLARED_UNSOURCED_IDS,
    LIVE_SOURCED_IDS,
    build_dashboard_metric_registry,
    reconcile_dashboard_snapshot,
)
from app.observability.metric_registry import STATUS_DEGRADED, STATUS_OK


def test_every_dashboard_metric_forbids_frontend_calculation() -> None:
    reg = build_dashboard_metric_registry({})
    for mid in ALL_METRIC_IDS:
        assert reg.is_frontend_calculation_allowed(mid) is False, mid


def test_live_sourced_metrics_serve_value() -> None:
    values: dict[str, float | None] = dict.fromkeys(LIVE_SOURCED_IDS, 3.0)
    reg = build_dashboard_metric_registry(values)
    for mid in LIVE_SOURCED_IDS:
        res = reg.serve(mid, now_ms=1_000_000)
        assert res.status == STATUS_OK, mid
        assert res.value == 3.0


def test_unsourced_metrics_serve_degraded_not_fabricated() -> None:
    # no values supplied for the declared-unsourced cohort
    reg = build_dashboard_metric_registry({})
    for mid in DECLARED_UNSOURCED_IDS:
        res = reg.serve(mid, now_ms=1_000_000)
        assert res.status == STATUS_DEGRADED, mid
        assert res.value is None, mid
        assert res.warning and "withheld" in res.warning


def test_missing_value_for_live_metric_degrades() -> None:
    # an explicit None (e.g. priority_lift not yet computable) degrades honestly
    reg = build_dashboard_metric_registry({"priority_tier_lift_pct": None})
    res = reg.serve("priority_tier_lift_pct", now_ms=1_000_000)
    assert res.status == STATUS_DEGRADED
    assert res.value is None


def test_builder_is_pure_same_input_same_output() -> None:
    values = {"paper_fills_recent_24h": 7.0}
    a = build_dashboard_metric_registry(values).serve("paper_fills_recent_24h", now_ms=1_000)
    b = build_dashboard_metric_registry(values).serve("paper_fills_recent_24h", now_ms=1_000)
    assert a.model_dump() == b.model_dump()


def test_reconcile_matching_snapshot_within_tolerance() -> None:
    values = {"paper_fills_recent_24h": 5.0, "priority_tier_lift_pct": 2.0}
    reg = build_dashboard_metric_registry(values)
    results = reconcile_dashboard_snapshot(reg, values, now_ms=1_000_000)
    assert results
    assert all(r.within_tolerance for r in results)


def test_reconcile_drift_flags_warning_not_raises() -> None:
    reg = build_dashboard_metric_registry({"paper_fills_recent_24h": 5.0})
    # external snapshot claims 12 fills → count tolerance is 0 → drift
    results = reconcile_dashboard_snapshot(reg, {"paper_fills_recent_24h": 12.0}, now_ms=1_000_000)
    assert len(results) == 1
    assert results[0].within_tolerance is False
    assert results[0].ssot_value == 5.0
    assert results[0].external_value == 12.0


def test_reconcile_unsourced_metric_never_ok() -> None:
    reg = build_dashboard_metric_registry({})
    results = reconcile_dashboard_snapshot(reg, {"var_usd": 100.0}, now_ms=1_000_000)
    assert results[0].within_tolerance is False
    assert results[0].reason.startswith("ssot_")
