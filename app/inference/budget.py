"""Optional spend governance backed by existing append-only telemetry."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.core.settings import InferenceSettings
from app.storage.jsonl_io import iter_jsonl_tolerant


@dataclass(frozen=True)
class BudgetDecision:
    allowed: bool
    reason: str | None
    soft_limit_exceeded: bool
    daily_spend_usd: float
    monthly_spend_usd: float
    unknown_cost_calls: int
    premium_calls_today: int


class BudgetGovernor:
    """Evaluate configured limits without pretending unknown cost equals zero."""

    def __init__(self, settings: InferenceSettings) -> None:
        self._settings = settings
        self._path = Path(settings.telemetry_path)

    def evaluate(
        self,
        *,
        route: str,
        estimated_request_cost_usd: float | None,
        now: datetime | None = None,
    ) -> BudgetDecision:
        current = now or datetime.now(UTC)
        day_cutoff = current.replace(hour=0, minute=0, second=0, microsecond=0)
        month_cutoff = day_cutoff.replace(day=1)
        daily = monthly = 0.0
        unknown = premium = 0
        if self._path.exists():
            for row in iter_jsonl_tolerant(self._path):
                if row.get("event_scope", "call") != "call" or not row.get("ok", False):
                    continue
                try:
                    ts = datetime.fromisoformat(str(row.get("ts", "")))
                except ValueError:
                    continue
                if ts < month_cutoff:
                    continue
                raw_cost = row.get("estimated_cost")
                if raw_cost is None:
                    unknown += 1
                else:
                    try:
                        cost = float(raw_cost)
                    except (TypeError, ValueError):
                        unknown += 1
                    else:
                        monthly += max(0.0, cost)
                        if ts >= day_cutoff:
                            daily += max(0.0, cost)
                if ts >= day_cutoff and row.get("logical_route") in {"reasoning", "critical"}:
                    premium += 1

        projected_daily = daily + (estimated_request_cost_usd or 0.0)
        projected_monthly = monthly + (estimated_request_cost_usd or 0.0)
        reason: str | None = None
        per_route = self._settings.per_route_max_cost_usd.get(route)
        if per_route is not None and estimated_request_cost_usd is not None:
            if estimated_request_cost_usd > per_route:
                reason = "per_route_hard_limit"
        if reason is None and self._settings.daily_hard_limit_usd is not None:
            if projected_daily >= self._settings.daily_hard_limit_usd:
                reason = "daily_hard_limit"
        if reason is None and self._settings.monthly_hard_limit_usd is not None:
            if projected_monthly >= self._settings.monthly_hard_limit_usd:
                reason = "monthly_hard_limit"
        if (
            reason is None
            and route in {"reasoning", "critical"}
            and self._settings.premium_escalation_daily_limit is not None
            and premium >= self._settings.premium_escalation_daily_limit
        ):
            reason = "premium_escalation_limit"

        # Critical calls fail closed when a hard spend ceiling is configured but
        # no request estimate exists. Other routes remain fail-soft and audit the
        # unknown cost, preserving the default behavior.
        hard_configured = any(
            limit is not None
            for limit in (
                self._settings.daily_hard_limit_usd,
                self._settings.monthly_hard_limit_usd,
                per_route,
            )
        )
        if reason is None and route == "critical" and hard_configured:
            if estimated_request_cost_usd is None:
                reason = "critical_cost_unknown"

        soft = False
        if self._settings.daily_soft_limit_usd is not None:
            soft = soft or projected_daily >= self._settings.daily_soft_limit_usd
        if self._settings.monthly_soft_limit_usd is not None:
            soft = soft or projected_monthly >= self._settings.monthly_soft_limit_usd

        return BudgetDecision(
            allowed=reason is None,
            reason=reason,
            soft_limit_exceeded=soft,
            daily_spend_usd=round(daily, 8),
            monthly_spend_usd=round(monthly, 8),
            unknown_cost_calls=unknown,
            premium_calls_today=premium,
        )
