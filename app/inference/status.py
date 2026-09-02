"""Read-only operator status for the operational inference layer."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.core.settings import AppSettings
from app.inference.budget import BudgetGovernor
from app.inference.evaluation import shadow_evaluation_summary
from app.inference.router import get_inference_router
from app.observability.llm_telemetry import llm_telemetry_summary


async def build_inference_status(settings: AppSettings) -> dict[str, Any]:
    router = get_inference_router()
    gateway_reachable: bool | None = None
    if settings.inference.enabled:
        gateway_reachable = await router.gateway_reachable()
    budget = BudgetGovernor(settings.inference).evaluate(
        route="standard",
        estimated_request_cost_usd=None,
    )
    return {
        "enabled": settings.inference.enabled,
        "configured_mode": settings.inference.mode,
        "mode": settings.inference.effective_mode,
        "gateway_reachable": gateway_reachable,
        "gateway_scope": "loopback"
        if not settings.inference.allow_non_loopback_gateway
        else "explicit_non_loopback",
        "configured_route_aliases": router.configured_routes,
        "default_route": settings.inference.route_aliases.get("standard"),
        "provider_readiness": {
            "gateway_auth": bool(settings.inference.gateway_api_key),
            "openai_direct_fallback": bool(settings.providers.openai_api_key),
            "anthropic_independent_shadow": bool(settings.providers.anthropic_api_key),
            "gemini_direct_fallback": bool(settings.providers.gemini_api_key),
            "xai_direct_fallback": bool(
                settings.providers.xai_fallback_enabled and settings.providers.xai_api_key
            ),
        },
        "circuit_status": router.circuit_snapshot,
        "telemetry_24h": llm_telemetry_summary(path=Path(settings.inference.telemetry_path)),
        "shadow_evaluation": shadow_evaluation_summary(
            Path(settings.inference.shadow_comparison_path)
        ),
        "budget_status": {
            "allowed": budget.allowed,
            "reason": budget.reason,
            "soft_limit_exceeded": budget.soft_limit_exceeded,
            "daily_spend_usd": budget.daily_spend_usd,
            "monthly_spend_usd": budget.monthly_spend_usd,
            "unknown_cost_calls": budget.unknown_cost_calls,
            "premium_calls_today": budget.premium_calls_today,
        },
        "execution_authority": False,
        "adr_0015_namespace_untouched": True,
    }
