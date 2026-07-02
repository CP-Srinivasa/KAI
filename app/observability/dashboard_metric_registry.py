"""Dashboard metric registry — Truth-Layer v2 wiring (Issue #170 Part A).

#162 introduced the formal :class:`~app.observability.metric_registry.MetricRegistry`
as a backing SSOT *without* wiring a live read path. This module performs the
deliberate follow-up wiring for the **dashboard truth contract**: it declares the
canonical scalar dashboard metrics once, binds each to a calculation function,
and lets the dashboard router serve the registry envelope alongside (and
reconciled against) its existing ``metric_contract``.

Design contract
---------------
- **One calculation source.** The registry is built from the *same* values the
  truth-contract already computed (passed in via ``values``); it does not open a
  second, divergent computation path.
- **Pure / IO-free.** The builder takes already-read values + source timestamps;
  it never touches disk. The router (which already loaded the artifacts) supplies
  them.
- **Honest absence.** A metric whose value is ``None`` (no source data yet —
  e.g. the risk scalars that still need an equity-return series) is served as
  ``status="degraded"`` with ``value=None`` — never a fabricated number.
- **Frontend never computes.** Every definition is ``frontend_calculation_allowed=False``;
  the dashboard asserts this guard.

Aspirational-but-unsourced metrics (var/cvar/sharpe/sortino/exposure/...) are
registered so the contract advertises them as first-class SSOT metrics, but they
serve ``degraded`` until their calculation bindings land (tracked in #170 + the
NEO-P-002-r3 feeder for the generator-edge side). This is intentional: a visible
``degraded`` badge is the honest state, not a hidden gap.
"""

from __future__ import annotations

from app.observability.metric_registry import (
    CalculationFn,
    MetricComputation,
    MetricDefinition,
    MetricRegistry,
    ReconcileResult,
)

# One version string for the whole dashboard metric cohort. Bump when any bound
# formula changes — a pinned caller (cached snapshot) then gets a drift warning.
CALCULATION_VERSION = "2026-06-08"

# Owner of the dashboard truth cohort.
_OWNER = "truth-layer-v2"


def _metric(
    metric_id: str,
    name: str,
    *,
    source_tables: tuple[str, ...],
    calculation_function: str,
    frequency: str,
    staleness_limit_ms: int,
    tolerance_abs: float,
    tolerance_pct: float,
) -> MetricDefinition:
    return MetricDefinition(
        metric_id=metric_id,
        name=name,
        owner=_OWNER,
        calculation_version=CALCULATION_VERSION,
        source_tables=source_tables,
        calculation_function=calculation_function,
        tolerance_abs=tolerance_abs,
        tolerance_pct=tolerance_pct,
        frequency=frequency,
        staleness_limit_ms=staleness_limit_ms,
        display_allowed=True,
        frontend_calculation_allowed=False,
    )


# Canonical scalar dashboard metrics. The first cohort is live-sourced from the
# truth contract; the second cohort is declared but serves ``degraded`` until a
# calculation binding is wired (issue #170 + risk-engine equity-series plumbing).
_LIVE_SOURCED: tuple[MetricDefinition, ...] = (
    _metric(
        "paper_fills_with_pnl",
        "Paper closed-trade activity (historical)",
        source_tables=("paper_execution_audit",),
        calculation_function="positions_closed + positions_partial_closed",
        frequency="realtime",
        staleness_limit_ms=86_400_000,  # historical/lifetime → generous
        tolerance_abs=0.0,
        tolerance_pct=0.0,
    ),
    _metric(
        "paper_fills_recent_24h",
        "Paper fills (rolling 24h)",
        source_tables=("paper_execution_audit",),
        calculation_function="count(fills in rolling 24h window)",
        frequency="realtime",
        staleness_limit_ms=3_600_000,
        tolerance_abs=0.0,
        tolerance_pct=0.0,
    ),
    _metric(
        "priority_tier_lift_pct",
        "Priority-tier hit-rate lift (P10 − P7..P9)",
        source_tables=("alert_audit",),
        calculation_function="quality.priority_tier_lift_pct",
        frequency="hourly",
        staleness_limit_ms=21_600_000,
        tolerance_abs=0.1,
        tolerance_pct=1.0,
    ),
    _metric(
        "source_reliability_trusted_count",
        "Trusted source count (Wilson tiers)",
        source_tables=("source_reliability_report",),
        calculation_function="source_reliability.trusted_count",
        frequency="daily",
        staleness_limit_ms=172_800_000,
        tolerance_abs=0.0,
        tolerance_pct=0.0,
    ),
)

# Declared SSOT metrics that have no live calculation binding yet → serve
# ``degraded`` (value=None) honestly. Listed here so the registry advertises the
# full critical-metric surface from #170 instead of hiding the gap.
_DECLARED_UNSOURCED: tuple[MetricDefinition, ...] = (
    _metric(
        "pnl_realized_usd",
        "Realized PnL (USD)",
        source_tables=("paper_execution_audit",),
        calculation_function="<pending: closed-trade realized PnL aggregation>",
        frequency="realtime",
        staleness_limit_ms=3_600_000,
        tolerance_abs=0.5,
        tolerance_pct=1.0,
    ),
    _metric(
        "pnl_unrealized_usd",
        "Unrealized PnL (USD)",
        source_tables=("paper_portfolio",),
        calculation_function="<pending: open-position MTM aggregation>",
        frequency="realtime",
        staleness_limit_ms=600_000,
        tolerance_abs=0.5,
        tolerance_pct=1.0,
    ),
    _metric(
        "fees_usd",
        "Fees paid (USD)",
        source_tables=("paper_execution_audit",),
        calculation_function="<pending: fee aggregation>",
        frequency="realtime",
        staleness_limit_ms=3_600_000,
        tolerance_abs=0.5,
        tolerance_pct=1.0,
    ),
    _metric(
        "exposure_gross_usd",
        "Gross exposure (USD)",
        source_tables=("paper_portfolio",),
        calculation_function="<pending: sum |notional| of open positions>",
        frequency="realtime",
        staleness_limit_ms=600_000,
        tolerance_abs=1.0,
        tolerance_pct=1.0,
    ),
    _metric(
        "exposure_net_usd",
        "Net exposure (USD)",
        source_tables=("paper_portfolio",),
        calculation_function="<pending: signed notional sum of open positions>",
        frequency="realtime",
        staleness_limit_ms=600_000,
        tolerance_abs=1.0,
        tolerance_pct=1.0,
    ),
    _metric(
        "drawdown_max_pct",
        "Max drawdown (%)",
        source_tables=("equity_curve",),
        calculation_function="<pending: PortfolioRiskEngine on equity-return series>",
        frequency="hourly",
        staleness_limit_ms=21_600_000,
        tolerance_abs=0.1,
        tolerance_pct=1.0,
    ),
    _metric(
        "var_usd",
        "Value-at-Risk (USD)",
        source_tables=("equity_curve",),
        calculation_function="<pending: PortfolioRiskEngine.compute().var>",
        frequency="hourly",
        staleness_limit_ms=21_600_000,
        tolerance_abs=1.0,
        tolerance_pct=2.0,
    ),
    _metric(
        "cvar_usd",
        "Conditional VaR (USD)",
        source_tables=("equity_curve",),
        calculation_function="<pending: PortfolioRiskEngine.compute().cvar>",
        frequency="hourly",
        staleness_limit_ms=21_600_000,
        tolerance_abs=1.0,
        tolerance_pct=2.0,
    ),
    _metric(
        "sharpe",
        "Sharpe ratio",
        source_tables=("equity_curve",),
        calculation_function="<pending: equity-return Sharpe>",
        frequency="daily",
        staleness_limit_ms=172_800_000,
        tolerance_abs=0.05,
        tolerance_pct=2.0,
    ),
    _metric(
        "sortino",
        "Sortino ratio",
        source_tables=("equity_curve",),
        calculation_function="<pending: equity-return Sortino>",
        frequency="daily",
        staleness_limit_ms=172_800_000,
        tolerance_abs=0.05,
        tolerance_pct=2.0,
    ),
    _metric(
        "win_rate_pct",
        "Win rate (%)",
        source_tables=("paper_execution_audit",),
        calculation_function="<pending: closed-trade win rate>",
        frequency="realtime",
        staleness_limit_ms=3_600_000,
        tolerance_abs=0.1,
        tolerance_pct=1.0,
    ),
)

ALL_DEFINITIONS: tuple[MetricDefinition, ...] = _LIVE_SOURCED + _DECLARED_UNSOURCED

# Public id tuples so the router / tests can iterate without re-deriving them.
LIVE_SOURCED_IDS: tuple[str, ...] = tuple(d.metric_id for d in _LIVE_SOURCED)
DECLARED_UNSOURCED_IDS: tuple[str, ...] = tuple(d.metric_id for d in _DECLARED_UNSOURCED)
ALL_METRIC_IDS: tuple[str, ...] = tuple(d.metric_id for d in ALL_DEFINITIONS)


def _make_binding(value: float | None, ts_ms: int | None, methodology: str) -> CalculationFn:
    """Return a zero-arg calculation function bound to one metric's value.

    A factory (rather than a loop lambda) keeps each closure's captured value
    correct and gives mypy a concrete ``CalculationFn`` type to check.
    """

    def _fn() -> MetricComputation:
        return MetricComputation(
            value=value,
            data_version=CALCULATION_VERSION,
            source_timestamp_ms=ts_ms,
            methodology=methodology,
        )

    return _fn


def build_dashboard_metric_registry(
    values: dict[str, float | None],
    *,
    source_timestamps_ms: dict[str, int | None] | None = None,
) -> MetricRegistry:
    """Build a populated registry from already-computed dashboard ``values``.

    ``values`` maps ``metric_id`` → scalar (or ``None`` for not-yet-available).
    Any declared metric absent from ``values`` is bound to ``None`` → served as
    ``degraded``. ``source_timestamps_ms`` optionally supplies a per-metric
    source timestamp (epoch ms) so staleness can be computed; absent → no
    staleness check (the value is still served).
    """
    ts_map = source_timestamps_ms or {}
    reg = MetricRegistry()
    for definition in ALL_DEFINITIONS:
        mid = definition.metric_id
        value = values.get(mid)
        ts_ms = ts_map.get(mid)
        methodology = definition.calculation_function
        reg.register(definition, _make_binding(value, ts_ms, methodology))
    return reg


def reconcile_dashboard_snapshot(
    registry: MetricRegistry,
    snapshot: dict[str, float],
    *,
    now_ms: int,
) -> list[ReconcileResult]:
    """Reconcile externally-presented ``snapshot`` values against the SSOT.

    ``snapshot`` maps ``metric_id`` → the value some other layer (cached view,
    legacy contract field) is showing. Returns one :class:`ReconcileResult` per
    snapshot entry. Drift is surfaced (``within_tolerance=False``) for the caller
    to log as a **warning** — never a hard fail (a divergent cache must not take
    the dashboard down).
    """
    return [
        registry.reconcile(metric_id, external_value, now_ms=now_ms)
        for metric_id, external_value in snapshot.items()
    ]


__all__ = [
    "ALL_DEFINITIONS",
    "ALL_METRIC_IDS",
    "CALCULATION_VERSION",
    "DECLARED_UNSOURCED_IDS",
    "LIVE_SOURCED_IDS",
    "build_dashboard_metric_registry",
    "reconcile_dashboard_snapshot",
]
