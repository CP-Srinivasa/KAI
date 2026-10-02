"""KI-Kontrollstation -- nur lesend (Stufe 1).

Auth: die Dashboard-Regel aus ``app/security/auth.py`` (D-124/D-156d) wie fuer alle
``/dashboard/api/*``: lokal (ohne Cf-Ray) offen, ueber den Tunnel nur mit CF-Access-
Allowlist. Kein Modellaufruf; der einzige Netzzugriff ist das lokale Lebenszeichen des
LiteLLM-Proxys ueber ``ai_transport_snapshot``.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Query, Response

router = APIRouter(tags=["ai-control"])


async def _transport(**_kw: Any) -> dict[str, Any] | None:
    from app.ai.runtime import inference_settings
    from app.ai.transport_status import ai_transport_snapshot, default_transport_paths

    try:
        configured = inference_settings(None)
        return await ai_transport_snapshot(
            settings=configured, paths=default_transport_paths(configured)
        )
    except Exception:  # noqa: BLE001 -- ohne Transport-Status zeigt die Seite einen Grund
        return None


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"


def providers_configured() -> dict[str, bool]:
    """Welche direkten Schluessel gesetzt sind -- nur ja/nein, nie der Wert."""
    from app.core.settings import get_settings

    p = get_settings().providers
    return {
        "openai": bool(p.openai_api_key),
        "anthropic": bool(p.anthropic_api_key),
        "gemini": bool(p.gemini_api_key),
        "xai": bool(p.xai_api_key),
    }


@router.get("/dashboard/api/ai/control")
async def ai_control(response: Response) -> dict[str, Any]:
    from app.ai.control.config import ControlPaths, ControlThresholds, LiteLLMModels
    from app.ai.control.snapshot import build_snapshot
    from app.ai.runtime import inference_settings

    _no_store(response)
    return build_snapshot(
        now=datetime.now(UTC),
        paths=ControlPaths(),
        inference=inference_settings(None),
        transport=await _transport(),
        thresholds=ControlThresholds(),
        models=LiteLLMModels(),
        providers_configured=providers_configured(),
    )


@router.get("/dashboard/api/ai/control/history")
async def ai_control_history(
    response: Response, days: int = Query(14, ge=1, le=31)
) -> dict[str, Any]:
    from app.ai.control import history
    from app.ai.control.config import ControlPaths
    from app.ai.spend import load_rows

    _no_store(response)
    tage = history.daily(load_rows(ControlPaths().telemetry), now=datetime.now(UTC), days=days)
    return {"schema": "ai-control-history/v1", "days": [asdict(t) for t in tage]}
