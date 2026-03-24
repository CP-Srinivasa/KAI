"""Alert Service — single entry-point for alert dispatch.

Usage:
    service = AlertService.from_settings(settings)
    results = await service.process_document(doc, result, spam_probability)

process_document():
  - Evaluates threshold (ThresholdEngine)
  - Returns [] if document does not meet threshold
  - Builds AlertMessage and dispatches to all active channels

send_digest():
  - Sends a pre-built list of AlertMessages as a digest to all channels

from_settings():
  - Factory method — reads AlertSettings and wires channels + threshold
  - In dry_run mode: channels are always included for logging
"""

from __future__ import annotations

from pathlib import Path

import structlog

from app.alerts.audit import ALERT_AUDIT_JSONL_FILENAME, AlertAuditRecord, append_alert_audit
from app.alerts.base.interfaces import AlertDeliveryResult, AlertMessage, BaseAlertChannel
from app.alerts.channels.email import EmailAlertChannel
from app.alerts.channels.telegram import TelegramAlertChannel
from app.alerts.threshold import ThresholdEngine
from app.analysis.scoring import compute_priority
from app.core.domain.document import AnalysisResult, CanonicalDocument
from app.core.settings import AppSettings

log = structlog.get_logger(__name__)

_WORKSPACE_ROOT = Path(__file__).resolve().parents[2]


class AlertService:
    """Orchestrates threshold evaluation and multi-channel alert delivery."""

    def __init__(
        self,
        channels: list[BaseAlertChannel],
        threshold: ThresholdEngine,
    ) -> None:
        self._channels = channels
        self._threshold = threshold

    @classmethod
    def from_settings(cls, settings: AppSettings) -> AlertService:
        """Build AlertService from AppSettings.

        Channels are only included when is_enabled=True.
        In dry_run mode, both channels are always added so output appears in logs.
        """
        s = settings.alerts
        channels: list[BaseAlertChannel] = []

        telegram = TelegramAlertChannel(s)
        if telegram.is_enabled or s.dry_run:
            channels.append(telegram)

        email = EmailAlertChannel(s)
        if email.is_enabled or s.dry_run:
            channels.append(email)

        threshold = ThresholdEngine(min_priority=s.min_priority)
        return cls(channels=channels, threshold=threshold)

    async def process_document(
        self,
        doc: CanonicalDocument,
        result: AnalysisResult,
        spam_probability: float = 0.0,
    ) -> list[AlertDeliveryResult]:
        """Evaluate threshold and dispatch alerts for one analyzed document.

        Returns:
            List of AlertDeliveryResult — one per active channel.
            Empty list if the document does not meet the alert threshold.
        """
        if not self._threshold.should_alert(result, spam_probability=spam_probability):
            return []

        message = _build_alert_message(doc, result, spam_probability)
        return await self._dispatch(message)

    async def send_digest(
        self,
        messages: list[AlertMessage],
        period: str,
    ) -> list[AlertDeliveryResult]:
        """Send a digest of multiple alerts to all active channels.

        period: human-readable label e.g. "last 60 minutes"
        """
        if not messages:
            return []
        results = []
        for channel in self._channels:
            delivery = await channel.send_digest(messages, period)
            _log_result(delivery, digest=True, document_id="multiple-digest")
            results.append(delivery)
        return results

    async def _dispatch(self, message: AlertMessage) -> list[AlertDeliveryResult]:
        results = []
        for channel in self._channels:
            delivery = await channel.send(message)
            _log_result(delivery, document_id=message.document_id)
            results.append(delivery)
        return results


def _build_alert_message(
    doc: CanonicalDocument,
    result: AnalysisResult,
    spam_probability: float,
) -> AlertMessage:
    """Build AlertMessage from CanonicalDocument + AnalysisResult."""
    score = compute_priority(result, spam_probability=spam_probability)
    return AlertMessage(
        document_id=str(doc.id),
        title=doc.title,
        url=doc.url,
        priority=score.priority,
        sentiment_label=str(result.sentiment_label),
        actionable=result.actionable,
        explanation=result.explanation_short,
        affected_assets=list(result.affected_assets),
        published_at=doc.published_at,
        source_name=doc.source_name,
        tags=list(result.tags),
    )


def _log_result(
    result: AlertDeliveryResult,
    *,
    digest: bool = False,
    document_id: str | None = None,
) -> None:
    kind = "digest" if digest else "alert"
    if result.success:
        log.info(
            f"alert.{kind}.sent",
            channel=result.channel,
            message_id=result.message_id,
        )
        # Sprint 21: Append audit trail for operational readiness
        if document_id:
            record = AlertAuditRecord(
                document_id=document_id,
                channel=result.channel,
                message_id=result.message_id,
                is_digest=digest,
            )
            audit_path = _WORKSPACE_ROOT / "artifacts" / ALERT_AUDIT_JSONL_FILENAME
            append_alert_audit(record, audit_path)
    else:
        log.error(
            f"alert.{kind}.failed",
            channel=result.channel,
            error=result.error,
        )
