from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel

from app.core.runtime_identity import drift_report, get_runtime_identity
from app.core.settings import AppSettings, get_settings
from app.services.timer_health import read_latest_timer_audit

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    version: str
    # STAB-02 (2026-08-25): Runtime-Identitaet. Vorher sah ein Server, der 7 Tage
    # hinter seinem Checkout lief, exakt so aus wie einer auf dem aktuellen Stand.
    # Alle Felder fail-soft (None = nicht messbar, NICHT 'aktuell').
    runtime_commit: str | None = None
    checkout_commit: str | None = None
    drift_commits: int | None = None
    started_at_utc: str | None = None
    uptime_s: float | None = None
    lock_changed: bool | None = None


# FS-2 (#198): "critical" added so a genuinely-stuck recurring timer is distinct
# from the benign "has_inactive" (which now only counts attention-worthy timers).
TimerHealthState = Literal["ok", "has_inactive", "stale", "no_data", "corrupt", "critical"]


class TimerHealthInactiveEntry(BaseModel):
    unit: str
    state: str
    # FS-2 taxonomy: recurring_required | one_shot_expected_inactive | disabled_by_design
    category: str | None = None
    # ok | expected_inactive | critical
    severity: str | None = None
    last_trigger: str | None = None


class TimerHealthResponse(BaseModel):
    state: TimerHealthState
    # FS-2: overall severity (ok | warning | critical) + taxonomy counts so the
    # dashboard can show "expected_inactive vs failed" instead of a blanket alarm.
    severity: str = "ok"
    checked_at: str | None = None
    stale_minutes: int | None = None
    total: int
    active: int
    critical_count: int = 0
    expected_inactive_count: int = 0
    inactive: list[TimerHealthInactiveEntry]


_RUNTIME_FIELDS = (
    "runtime_commit",
    "checkout_commit",
    "drift_commits",
    "started_at_utc",
    "uptime_s",
    "lock_changed",
)


def _runtime_fields() -> dict[str, object]:
    """Runtime vs. Checkout — nie ein 500 auf /health, egal was git tut."""
    try:
        report = drift_report(get_runtime_identity())
    except Exception:  # noqa: BLE001 - Liveness darf an der Identitaet nicht sterben
        return {}
    return {key: report.get(key) for key in _RUNTIME_FIELDS}


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok", version="0.1.0", **_runtime_fields())  # type: ignore[arg-type]


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
