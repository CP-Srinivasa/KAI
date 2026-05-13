"""Alerts API router.

Endpoints:
    POST /alerts/test  — send a synthetic test alert through all configured channels
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.alerts.base.interfaces import AlertMessage
from app.alerts.service import AlertService

router = APIRouter(prefix="/alerts", tags=["alerts"])


class AlertDeliveryResponse(BaseModel):
    channel: str
    success: bool
    message_id: str | None = None
    error: str | None = None


class AlertTestResponse(BaseModel):
    dispatched: int
    results: list[AlertDeliveryResponse]


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
