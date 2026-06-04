"""Formal metric registry — Truth-Layer v2 (NEO /goal 2026-06-05, Aufgabe 2).

Hardens the existing dashboard truth contract (``dashboard_truth_contract_version``
in ``app/api/routers/dashboard.py``, today per-call API metadata) into a single
authoritative source of truth: **every critical metric is computed in exactly
one place, versioned, and the frontend only displays — never recomputes.**

This module is the backing SSOT. Wiring the dashboard router to consume it is a
deliberate, separate follow-up (the dashboard's ``_metric_contract`` stays as-is
for now — we do not rewire a live read path in the same change that introduces
the registry).

Honesty contracts (KAI-Directive §9, kai-master-coding-regeln §safe)
--------------------------------------------------------------------
- A metric with no registry entry is **never served** (``status="unknown_metric"``,
  value ``None``). No ad-hoc metric leaks past the SSOT.
- Missing source data yields ``status="degraded"`` with value ``None`` — never a
  fabricated number (no 0.0-implies-flat trap).
- Stale source data (older than ``staleness_limit_ms``) yields ``status="stale"``
  with a warning; the value is still surfaced but flagged.
- A caller pinned to an outdated ``calculation_version`` (e.g. a cached snapshot
  computed under an older formula) gets an explicit warning — silent formula
  drift is the exact failure this layer exists to prevent.
- Reconciliation tolerates only the deviations declared on the definition
  (``tolerance_abs`` OR ``tolerance_pct``); anything beyond is a hard mismatch.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

STATUS_OK = "ok"
STATUS_STALE = "stale"
STATUS_DEGRADED = "degraded"
STATUS_UNKNOWN = "unknown_metric"


class MetricDefinition(BaseModel):
    """Authoritative definition of one metric. Frozen — definitions are SSOT."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    metric_id: str
    name: str
    owner: str
    calculation_version: str
    source_tables: tuple[str, ...]
    calculation_function: str  # human-readable reference to the bound callable
    tolerance_abs: float = Field(ge=0.0)
    tolerance_pct: float = Field(ge=0.0)
    frequency: str  # e.g. "realtime" | "1m" | "5m" | "hourly" | "daily"
    staleness_limit_ms: int = Field(gt=0)
    display_allowed: bool = True
    frontend_calculation_allowed: bool = False


class MetricComputation(BaseModel):
    """What a bound calculation function returns. ``value=None`` ⇒ no data."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    value: float | None
    data_version: str
    source_timestamp_ms: int | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    methodology: str = ""


class MetricResponse(BaseModel):
    """The single envelope the API serves. The frontend renders this verbatim."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    metric_id: str
    value: float | None
    timestamp_utc: str | None
    data_version: str | None
    calculation_version: str | None
    staleness_ms: int | None
    confidence: float | None
    methodology: str
    status: str  # ok | stale | degraded | unknown_metric
    warning: str | None = None


class ReconcileResult(BaseModel):
    """Outcome of comparing an externally-computed value against the SSOT value."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    metric_id: str
    within_tolerance: bool
    ssot_value: float | None
    external_value: float | None
    deviation_abs: float | None
    deviation_pct: float | None
    reason: str


CalculationFn = Callable[[], MetricComputation]


class MetricRegistry:
    """In-process registry binding definitions to their calculation functions.

    The registry is the ONLY sanctioned path to a critical metric's value.
    """

    def __init__(self) -> None:
        self._defs: dict[str, MetricDefinition] = {}
        self._fns: dict[str, CalculationFn] = {}

    # --- registration ----------------------------------------------------------

    def register(self, definition: MetricDefinition, fn: CalculationFn) -> None:
        """Register a metric. Re-registering the same id is rejected (no silent
        override of the SSOT — that would defeat the purpose)."""
        if definition.metric_id in self._defs:
            raise ValueError(f"metric already registered: {definition.metric_id}")
        self._defs[definition.metric_id] = definition
        self._fns[definition.metric_id] = fn

    def definition(self, metric_id: str) -> MetricDefinition | None:
        return self._defs.get(metric_id)

    def metric_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._defs))

    def is_frontend_calculation_allowed(self, metric_id: str) -> bool:
        """Guard the dashboard layer can assert against. Unknown metrics are
        treated as NOT allowed (fail-closed)."""
        d = self._defs.get(metric_id)
        return bool(d and d.frontend_calculation_allowed)

    # --- serving ---------------------------------------------------------------

    def serve(
        self,
        metric_id: str,
        *,
        now_ms: int,
        timestamp_utc: str | None = None,
        expected_calculation_version: str | None = None,
    ) -> MetricResponse:
        """Compute and wrap a metric in the authoritative envelope.

        ``expected_calculation_version`` lets a caller (e.g. a cached snapshot)
        declare which formula version it was built against; a mismatch with the
        registry's current version surfaces a warning instead of silently
        serving a value computed under a different formula.
        """
        definition = self._defs.get(metric_id)
        if definition is None:
            return MetricResponse(
                metric_id=metric_id,
                value=None,
                timestamp_utc=timestamp_utc,
                data_version=None,
                calculation_version=None,
                staleness_ms=None,
                confidence=None,
                methodology="",
                status=STATUS_UNKNOWN,
                warning=f"metric '{metric_id}' is not in the registry — refusing to serve",
            )

        comp = self._fns[metric_id]()
        warnings: list[str] = []

        if (
            expected_calculation_version is not None
            and expected_calculation_version != definition.calculation_version
        ):
            warnings.append(
                f"calculation_version mismatch: caller pinned "
                f"{expected_calculation_version!r}, registry is "
                f"{definition.calculation_version!r}"
            )

        # Missing data → degraded, never a fabricated value.
        if comp.value is None:
            return MetricResponse(
                metric_id=metric_id,
                value=None,
                timestamp_utc=timestamp_utc,
                data_version=comp.data_version,
                calculation_version=definition.calculation_version,
                staleness_ms=None,
                confidence=comp.confidence,
                methodology=comp.methodology,
                status=STATUS_DEGRADED,
                warning="; ".join([*warnings, "no source data — value withheld"]),
            )

        staleness_ms: int | None = None
        status = STATUS_OK
        if comp.source_timestamp_ms is not None:
            staleness_ms = max(0, now_ms - comp.source_timestamp_ms)
            if staleness_ms > definition.staleness_limit_ms:
                status = STATUS_STALE
                warnings.append(
                    f"stale: {staleness_ms}ms > limit {definition.staleness_limit_ms}ms"
                )

        return MetricResponse(
            metric_id=metric_id,
            value=comp.value,
            timestamp_utc=timestamp_utc,
            data_version=comp.data_version,
            calculation_version=definition.calculation_version,
            staleness_ms=staleness_ms,
            confidence=comp.confidence,
            methodology=comp.methodology,
            status=status,
            warning="; ".join(warnings) if warnings else None,
        )

    # --- reconciliation --------------------------------------------------------

    def reconcile(self, metric_id: str, external_value: float, *, now_ms: int) -> ReconcileResult:
        """Compare an externally-computed value against the SSOT value.

        Within tolerance iff deviation_abs <= tolerance_abs OR deviation_pct <=
        tolerance_pct (either bound passing is enough — abs guards small
        magnitudes, pct guards large ones). Unknown/degraded metrics never
        reconcile as OK.
        """
        definition = self._defs.get(metric_id)
        if definition is None:
            return ReconcileResult(
                metric_id=metric_id,
                within_tolerance=False,
                ssot_value=None,
                external_value=external_value,
                deviation_abs=None,
                deviation_pct=None,
                reason="unknown_metric",
            )

        served = self.serve(metric_id, now_ms=now_ms)
        if served.value is None:
            return ReconcileResult(
                metric_id=metric_id,
                within_tolerance=False,
                ssot_value=None,
                external_value=external_value,
                deviation_abs=None,
                deviation_pct=None,
                reason=f"ssot_{served.status}",
            )

        ssot = served.value
        dev_abs = abs(ssot - external_value)
        dev_pct = (
            (dev_abs / abs(ssot) * 100.0) if ssot != 0.0 else (0.0 if dev_abs == 0.0 else 100.0)
        )
        within = dev_abs <= definition.tolerance_abs or dev_pct <= definition.tolerance_pct
        return ReconcileResult(
            metric_id=metric_id,
            within_tolerance=within,
            ssot_value=ssot,
            external_value=external_value,
            deviation_abs=dev_abs,
            deviation_pct=dev_pct,
            reason="within_tolerance" if within else "deviation_exceeds_tolerance",
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise all definitions (for a registry-introspection endpoint)."""
        return {
            "metric_count": len(self._defs),
            "metrics": {mid: d.model_dump() for mid, d in sorted(self._defs.items())},
        }
