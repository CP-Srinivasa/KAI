"""
Alert Management Endpoints
===========================
GET  /alerts/              — list recent alerts
GET  /alerts/rules         — list configured alert rules
POST /alerts/test          — send a test alert [REQUIRES: channel config in .env]
POST /alerts/preview       — preview alert message (dry-run, no send)
GET  /alerts/stats         — channel delivery statistics
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.rules import DEFAULT_RULES
from app.core.enums import AlertChannel, DocumentPriority
from app.storage.db.session import get_db_session
from app.storage.repositories.alert_repo import AlertRepository

router = APIRouter()


class PreviewRequest(BaseModel):
    title: str = "Test Alert: Bitcoin ETF Approved"
    explanation_short: str = "The SEC has approved a spot Bitcoin ETF application."
    sentiment_label: str = "positive"
    sentiment_score: float = 0.75
    impact_score: float = 0.82
    relevance_score: float = 0.78
    credibility_score: float = 0.71
    affected_assets: list[str] = ["BTC", "ETH"]
    url: str = ""


@router.get("/rules")
async def list_rules() -> dict[str, Any]:
    """List all configured alert rules with their thresholds."""
    return {
        "rules": [r.to_dict() for r in DEFAULT_RULES],
        "total": len(DEFAULT_RULES),
        "enabled": sum(1 for r in DEFAULT_RULES if r.enabled),
    }


@router.post("/preview")
async def preview_alert(request: PreviewRequest) -> dict[str, Any]:
    """
    Preview formatted alert messages (Telegram + Email) without sending.
    Useful for validating formatting before configuring channels.
    """
    from app.alerts.evaluator import DocumentScores
    from app.integrations.telegram.adapter import format_breaking_alert
    from app.integrations.email.adapter import format_breaking_text, format_breaking_html

    scores = DocumentScores(
        document_id="preview-doc",
        source_id="preview",
        title=request.title,
        explanation_short=request.explanation_short,
        sentiment_label=request.sentiment_label,
        sentiment_score=request.sentiment_score,
        impact_score=request.impact_score,
        relevance_score=request.relevance_score,
        credibility_score=request.credibility_score,
        affected_assets=request.affected_assets,
        url=request.url,
        recommended_priority=DocumentPriority.HIGH,
    )

    return {
        "telegram_markdown": format_breaking_alert(scores),
        "email_text": format_breaking_text(scores),
        "email_html_chars": len(format_breaking_html(scores)),
        "note": "Preview only — no message was sent",
    }


@router.post("/test")
async def send_test_alert(
    channel: str = Query("telegram", description="Channel: telegram or email"),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """
    Send a test alert to the specified channel.
    [REQUIRES: TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID + TELEGRAM_ENABLED=true for telegram]
    [REQUIRES: EMAIL_* settings + EMAIL_ENABLED=true for email]
    """
    from app.alerts.evaluator import AlertDecision, DocumentScores
    from app.core.settings import get_settings
    from app.core.enums import AlertType

    settings = get_settings()

    test_scores = DocumentScores(
        document_id="test-alert-id",
        source_id="test",
        title="🧪 Test Alert — AI Analyst Bot is online",
        explanation_short="This is a test alert to verify your channel configuration.",
        sentiment_label="positive",
        sentiment_score=0.5,
        impact_score=0.6,
        relevance_score=0.7,
        credibility_score=0.8,
        novelty_score=1.0,
        affected_assets=["BTC", "TEST"],
        recommended_priority=DocumentPriority.MEDIUM,
    )

    try:
        ch = AlertChannel(channel)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unknown channel '{channel}'. Use: telegram, email")

    decision = AlertDecision(
        rule_name="test_alert",
        alert_type=AlertType.BREAKING,
        channels=[ch],
        should_alert=True,
        severity=DocumentPriority.MEDIUM,
        reasons=["manual test"],
        document_scores=test_scores,
    )

    if ch == AlertChannel.TELEGRAM:
        if not settings.telegram.is_configured:
            return {
                "status": "not_configured",
                "message": "Set TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_ENABLED=true in .env",
            }
        from app.integrations.telegram.adapter import TelegramAdapter
        adapter = TelegramAdapter(
            bot_token=settings.telegram.bot_token,
            chat_id=settings.telegram.chat_id,
        )
        success = await adapter.send_alert(decision)

    elif ch == AlertChannel.EMAIL:
        if not settings.email.is_configured:
            return {
                "status": "not_configured",
                "message": "Set EMAIL_SMTP_USER, EMAIL_SMTP_PASSWORD, EMAIL_FROM, EMAIL_TO, EMAIL_ENABLED=true in .env",
            }
        from app.integrations.email.adapter import EmailAdapter
        adapter = EmailAdapter(
            smtp_host=settings.email.smtp_host,
            smtp_port=settings.email.smtp_port,
            smtp_user=settings.email.smtp_user,
            smtp_password=settings.email.smtp_password,
            from_address=settings.email.from_address,
            to_address=settings.email.to_address,
            use_tls=settings.email.use_tls,
        )
        success = await adapter.send_alert(decision)
    else:
        return {"status": "not_implemented", "channel": channel}

    return {"status": "sent" if success else "failed", "channel": channel, "success": success}


@router.get("/stats")
async def alert_stats(
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Return delivery statistics by channel."""
    repo = AlertRepository(session)
    by_channel = await repo.count_by_channel()
    recent = await repo.list_recent(limit=5)
    return {
        "by_channel": by_channel,
        "total": sum(by_channel.values()),
        "recent": [
            {
                "id": str(a.id),
                "alert_type": a.alert_type,
                "channel": a.channel,
                "title": a.title[:80],
                "sent_at": a.sent_at.isoformat(),
                "success": a.success,
            }
            for a in recent
        ],
    }


@router.get("/")
async def list_alerts(
    limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """List recent alert sends from the database."""
    repo = AlertRepository(session)
    alerts = await repo.list_recent(limit=limit)
    return {
        "alerts": [
            {
                "id": str(a.id),
                "alert_type": a.alert_type,
                "channel": a.channel,
                "title": a.title[:100],
                "sent_at": a.sent_at.isoformat(),
                "success": a.success,
                "error": a.error,
            }
            for a in alerts
        ],
        "total": len(alerts),
    }
