"""FastAPI route: GET /health/premium_pipeline (P0 #4 — 2026-05-14).

Returns HTTP 200 + report when all pipeline checks pass; HTTP 503 + report
when any critical check fails. Mounted alongside the existing /health route
but lives in its own router so the trivial /health (server-liveness) stays
fast and dependency-free.

Why separate from /health: /health is hit by load balancers + uptime monitors
that DON'T want to depend on systemd-DBus reachability. /health/premium_pipeline
is hit by operator dashboards + the Telegram-alert cron — those callers DO
want full pipeline introspection.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.observability.premium_pipeline_health import compute_pipeline_health

router = APIRouter(tags=["health"])


@router.get("/health/premium_pipeline")
async def premium_pipeline_health() -> JSONResponse:
    report = compute_pipeline_health()
    status_code = 200 if report.healthy else 503
    return JSONResponse(content=report.to_dict(), status_code=status_code)
