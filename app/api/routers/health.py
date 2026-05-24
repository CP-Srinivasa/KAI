from pathlib import Path

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel

from app.core.settings import AppSettings, get_settings
from app.services.timer_health import read_latest_timer_audit

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    version: str


class TimerHealthInactiveEntry(BaseModel):
    unit: str
    state: str
    last_trigger: str | None = None


class TimerHealthResponse(BaseModel):
    state: str
    checked_at: str | None = None
    stale_minutes: int | None = None
    total: int
    active: int
    inactive: list[TimerHealthInactiveEntry]


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok", version="0.1.0")


@router.get("/health/timers", response_model=TimerHealthResponse)
async def timer_health(
    response: Response,
    settings: AppSettings = Depends(get_settings),  # noqa: B008
) -> TimerHealthResponse:
    """Read and return systemd-timer health audit logs (DALI-P-101)."""
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"

    workspace_root = Path(__file__).resolve().parents[3]
    audit_file = workspace_root / "artifacts" / "timer_health_audit.jsonl"
    data = read_latest_timer_audit(audit_file)
    return TimerHealthResponse(**data)

