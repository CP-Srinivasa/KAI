"""
Alert Repository
================
CRUD operations for Alert records.
Includes deduplication check: was this doc already alerted within N hours?
"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.storage.models.db_models import Alert

logger = get_logger(__name__)


class AlertRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(
        self,
        alert_type: str,
        channel: str,
        title: str = "",
        message: str = "",
        document_id: UUID | None = None,
        success: bool = True,
        error: str | None = None,
    ) -> Alert:
        alert = Alert(
            document_id=document_id,
            alert_type=alert_type,
            channel=channel,
            title=title,
            message=message,
            sent_at=datetime.utcnow(),
            success=success,
            error=error,
        )
        self._session.add(alert)
        logger.debug(
            "alert_saved",
            alert_type=alert_type,
            channel=channel,
            success=success,
        )
        return alert

    async def recently_sent(
        self,
        doc_id: str,
        rule_name: str,
        channel: str,
        window_hours: int = 24,
    ) -> bool:
        """
        Return True if an alert for this document+rule+channel was already sent
        within the dedup window. Used to suppress duplicate alerts.
        """
        if window_hours <= 0:
            return False
        try:
            doc_uuid = UUID(doc_id)
        except (ValueError, TypeError):
            return False

        cutoff = datetime.utcnow() - timedelta(hours=window_hours)
        result = await self._session.execute(
            select(Alert).where(
                Alert.document_id == doc_uuid,
                Alert.channel == channel,
                Alert.message == rule_name,   # rule_name stored in message field
                Alert.sent_at >= cutoff,
                Alert.success.is_(True),
            )
        )
        return result.scalar_one_or_none() is not None

    async def list_recent(self, limit: int = 50) -> list[Alert]:
        result = await self._session.execute(
            select(Alert).order_by(Alert.sent_at.desc()).limit(limit)
        )
        return list(result.scalars().all())

    async def count_by_channel(self) -> dict[str, int]:
        from sqlalchemy import func
        result = await self._session.execute(
            select(Alert.channel, func.count().label("count")).group_by(Alert.channel)
        )
        return {row.channel: row.count for row in result}
