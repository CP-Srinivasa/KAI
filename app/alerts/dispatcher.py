"""
Alert Dispatcher
================
Routes AlertDecisions to the correct channel adapters (Telegram, Email, Webhook).
Persists send attempts to the database.
Handles deduplication: skips alerts already sent within the dedup window.

Usage:
    dispatcher = AlertDispatcher(
        telegram=TelegramAdapter(...),
        email=EmailAdapter(...),
        alert_repo=AlertRepository(session),
    )
    await dispatcher.dispatch(decision)
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from app.alerts.evaluator import AlertDecision, DocumentScores
from app.core.enums import AlertChannel
from app.core.logging import get_logger

logger = get_logger(__name__)


class AlertDispatcher:
    """
    Routes AlertDecision objects to configured channel adapters.

    Only channels that are registered AND configured receive alerts.
    Missing adapters are skipped with a warning (not an error).

    Args:
        telegram:   Optional TelegramAdapter instance
        email:      Optional EmailAdapter instance
        alert_repo: Optional AlertRepository for persistence + deduplication
        dry_run:    If True, all sends are no-ops (overrides adapter dry_run)
    """

    def __init__(
        self,
        telegram: "Any | None" = None,
        email: "Any | None" = None,
        alert_repo: "Any | None" = None,
        dry_run: bool = False,
    ) -> None:
        self._telegram = telegram
        self._email = email
        self._alert_repo = alert_repo
        self._dry_run = dry_run

    async def dispatch(self, decision: AlertDecision) -> dict[str, bool]:
        """
        Send an alert to all channels specified in the decision.
        Returns {channel: success} mapping.
        """
        if not decision.should_alert:
            return {}

        results: dict[str, bool] = {}

        for channel in decision.channels:
            already_sent = await self._is_duplicate(decision, channel)
            if already_sent:
                logger.info(
                    "alert_dedup_skip",
                    rule=decision.rule_name,
                    channel=channel.value,
                )
                results[channel.value] = True  # Not an error, just skipped
                continue

            success = await self._send_to_channel(decision, channel)
            results[channel.value] = success
            await self._persist(decision, channel, success)

        return results

    async def dispatch_digest(
        self,
        items: list[DocumentScores],
        channels: list[AlertChannel],
        period: str = "Daily",
    ) -> dict[str, bool]:
        """Send a digest to multiple channels."""
        results: dict[str, bool] = {}
        for channel in channels:
            success = await self._send_digest_to_channel(items, channel, period)
            results[channel.value] = success
            logger.info(
                "digest_sent",
                channel=channel.value,
                items=len(items),
                success=success,
            )
        return results

    async def _send_to_channel(self, decision: AlertDecision, channel: AlertChannel) -> bool:
        if self._dry_run:
            logger.info(
                "dispatcher_dry_run",
                channel=channel.value,
                rule=decision.rule_name,
                doc_id=decision.document_scores.document_id if decision.document_scores else "?",
            )
            return True

        if channel == AlertChannel.TELEGRAM:
            if self._telegram is None:
                logger.warning("telegram_adapter_not_configured")
                return False
            return await self._telegram.send_alert(decision)

        if channel == AlertChannel.EMAIL:
            if self._email is None:
                logger.warning("email_adapter_not_configured")
                return False
            return await self._email.send_alert(decision)

        if channel == AlertChannel.WEBHOOK:
            logger.warning("webhook_not_implemented", channel=channel.value)
            return False

        logger.warning("unknown_channel", channel=channel.value)
        return False

    async def _send_digest_to_channel(
        self,
        items: list[DocumentScores],
        channel: AlertChannel,
        period: str,
    ) -> bool:
        if self._dry_run:
            logger.info("dispatcher_digest_dry_run", channel=channel.value, items=len(items))
            return True

        if channel == AlertChannel.TELEGRAM:
            if self._telegram is None:
                logger.warning("telegram_adapter_not_configured")
                return False
            return await self._telegram.send_digest(items, period)

        if channel == AlertChannel.EMAIL:
            if self._email is None:
                logger.warning("email_adapter_not_configured")
                return False
            return await self._email.send_digest(items, period)

        return False

    async def _is_duplicate(self, decision: AlertDecision, channel: AlertChannel) -> bool:
        """Check if we've already sent this alert recently (deduplication)."""
        if self._alert_repo is None:
            return False
        doc_id = (
            decision.document_scores.document_id
            if decision.document_scores
            else None
        )
        if not doc_id:
            return False
        return await self._alert_repo.recently_sent(
            doc_id=doc_id,
            rule_name=decision.rule_name,
            channel=channel.value,
        )

    async def _persist(
        self, decision: AlertDecision, channel: AlertChannel, success: bool
    ) -> None:
        """Persist alert send attempt to DB."""
        if self._alert_repo is None:
            return
        scores = decision.document_scores
        doc_id: UUID | None = None
        if scores:
            try:
                doc_id = UUID(scores.document_id)
            except (ValueError, TypeError):
                pass

        await self._alert_repo.save(
            document_id=doc_id,
            alert_type=decision.alert_type.value,
            channel=channel.value,
            title=scores.title if scores else "",
            message=decision.rule_name,
            success=success,
        )
