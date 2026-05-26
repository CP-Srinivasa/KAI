"""Alerts API router.

Endpoints:
    POST /alerts/test                  — send a synthetic test alert through all channels
    GET  /alerts/auto-annotate-report  — V5-Followup cohort report for forensic UI drawer
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.alerts.base.interfaces import AlertMessage
from app.alerts.service import AlertService

router = APIRouter(prefix="/alerts", tags=["alerts"])


def get_audit_dir() -> Path:
    """Return the audit directory used by reporting + auto-annotate.

    Hardcoded to ``artifacts/`` to mirror the existing CLI
    (``app.cli.main.alerts_auto_annotate_report``). Overridable in tests via
    FastAPI ``dependency_overrides``.
    """
    return Path("artifacts")


class AlertDeliveryResponse(BaseModel):
    channel: str
    success: bool
    message_id: str | None = None
    error: str | None = None


class AlertTestResponse(BaseModel):
    dispatched: int
    results: list[AlertDeliveryResponse]


class CohortCounters(BaseModel):
    total: int
    hit: int
    miss: int
    inconclusive: int
    resolved: int
    hit_rate_pct: float | None = None
    inconclusive_pct: float | None = None


class LatestPerDocCohort(CohortCounters):
    raw_rows: int
    unique_document_ids: int
    duplicate_rows_removed: int


class FreshDispatchCohort(CohortCounters):
    missing_audit: int


class CohortsBundle(BaseModel):
    fresh_auto: CohortCounters
    backfill: CohortCounters
    reeval: CohortCounters
    other: CohortCounters
    latest_per_doc: LatestPerDocCohort
    fresh_dispatch: FreshDispatchCohort


class ReportWindow(BaseModel):
    since: str | None = None
    until: str | None = None
    timestamp_basis: str


class AutoAnnotateReportResponse(BaseModel):
    window: ReportWindow
    raw_rows: int
    invalid_timestamp: int
    cohorts: CohortsBundle
    generated_at: str


@router.post("/test", response_model=AlertTestResponse)
async def send_test_alert(request: Request) -> AlertTestResponse:
    """Send a synthetic test alert to all configured channels.

    Uses the AlertService from app settings — respects dry_run mode.
    Returns one result per active channel.
    """
    from app.core.settings import get_settings

    settings = get_settings()
    service = AlertService.from_settings(settings)

    msg = AlertMessage(
        document_id="test-api-000",
        title="KAI Alert System — API Test",
        url="https://example.com/test",
        priority=8,
        sentiment_label="bullish",
        actionable=True,
        explanation="Test alert triggered via POST /alerts/test.",
        affected_assets=["BTC"],
        source_name="KAI API",
        tags=["test"],
        published_at=datetime.now(UTC),
    )

    raw_results = await service.send_digest([msg], "api-test")
    return AlertTestResponse(
        dispatched=len(raw_results),
        results=[
            AlertDeliveryResponse(
                channel=r.channel,
                success=r.success,
                message_id=r.message_id,
                error=r.error,
            )
            for r in raw_results
        ],
    )


@router.get("/auto-annotate-report", response_model=AutoAnnotateReportResponse)
def get_auto_annotate_report(
    since: str | None = None,
    until: str | None = None,
    dispatched_window: bool = False,
    audit_dir: Path = Depends(get_audit_dir),  # noqa: B008
) -> AutoAnnotateReportResponse:
    """V5-Followup cohort report — feeds the AutoAnnotateCohortDrawer (DALI-P-102).

    Splits annotated alert outcomes into the 6 V5 cohorts (fresh_auto, backfill,
    reeval, other, latest_per_doc, fresh_dispatch) for forensic UI rendering.
    Mirrors the CLI ``alerts auto-annotate-report`` semantics so operator and
    dashboard see identical numbers.

    - ``since`` / ``until``: ISO date or datetime string (UTC). When BOTH are
      omitted, the window defaults to the last 7 days.
    - ``dispatched_window=true`` filters by ``dispatched_at`` (from alert_audit)
      instead of ``annotated_at``. Required for forensic alignment with the
      Dispatch-Filter-Root-Befund (memory: kai_dispatch_filter_root_befund_20260524).
    """
    from app.alerts.reporting import generate_cohort_report, parse_utc_timestamp

    since_dt = parse_utc_timestamp(since) if since else None
    until_dt = parse_utc_timestamp(until) if until else None

    if since is not None and since_dt is None:
        raise HTTPException(status_code=400, detail=f"Invalid 'since' timestamp: {since!r}")
    if until is not None and until_dt is None:
        raise HTTPException(status_code=400, detail=f"Invalid 'until' timestamp: {until!r}")

    if since_dt is None and until_dt is None:
        since_dt = datetime.now(UTC) - timedelta(days=7)

    report = generate_cohort_report(
        audit_dir=audit_dir,
        since=since_dt,
        until=until_dt,
        use_dispatched_at=dispatched_window,
    )

    return AutoAnnotateReportResponse(
        window=ReportWindow(**report["window"]),
        raw_rows=report["raw_rows"],
        invalid_timestamp=report["invalid_timestamp"],
        cohorts=CohortsBundle(**report["cohorts"]),
        generated_at=datetime.now(UTC).isoformat(),
    )
