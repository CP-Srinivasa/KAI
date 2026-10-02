"""KI-Kontrollstation -- nur lesend (Stufe 1).

Auth: die Dashboard-Regel aus ``app/security/auth.py`` (D-124/D-156d) wie fuer alle
``/dashboard/api/*``: lokal (ohne Cf-Ray) offen, ueber den Tunnel nur mit CF-Access-
Allowlist. Kein Modellaufruf; der einzige Netzzugriff ist das lokale Lebenszeichen des
LiteLLM-Proxys ueber ``ai_transport_snapshot``.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Query, Response

router = APIRouter(tags=["ai-control"])


async def _transport(**_kw: Any) -> dict[str, Any] | None:
    from app.observability.ai_control.snapshot import current_transport

    return await current_transport()


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"


@router.get("/dashboard/api/ai/control")
async def ai_control(response: Response) -> dict[str, Any]:
    from app.ai.runtime import inference_settings
    from app.observability.ai_control import snapshot
    from app.observability.ai_control.config import (
        ControlPaths,
        ControlThresholds,
        LiteLLMModels,
        providers_configured,
    )

    _no_store(response)
    transport = await _transport()

    def bauen() -> dict[str, Any]:
        # Vollscan der Telemetrie und mehrfaches .env-Lesen -- im Thread, damit der
        # Event-Loop von kai-server (der auch die Inferenz traegt) frei bleibt.
        return snapshot.build_snapshot(
            now=datetime.now(UTC),
            paths=ControlPaths(),
            inference=inference_settings(None),
            transport=transport,
            thresholds=ControlThresholds(),
            models=LiteLLMModels(),
            providers_configured=providers_configured(),
        )

    return await asyncio.to_thread(bauen)


@router.get("/dashboard/api/ai/control/history")
async def ai_control_history(
    response: Response, days: int = Query(14, ge=1, le=31)
) -> dict[str, Any]:
    from app.ai.spend import load_rows
    from app.observability.ai_control import history
    from app.observability.ai_control.config import ControlPaths

    _no_store(response)

    def bauen() -> list[Any]:
        return history.daily(load_rows(ControlPaths().telemetry), now=datetime.now(UTC), days=days)

    tage = await asyncio.to_thread(bauen)
    return {"schema": "ai-control-history/v1", "days": [asdict(t) for t in tage]}
