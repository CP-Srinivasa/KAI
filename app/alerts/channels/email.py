"""Email alert channel.

Sends alerts via SMTP with STARTTLS (stdlib smtplib).
- In dry_run mode: no real SMTP connection is opened.
- SMTP is synchronous — runs in executor to stay non-blocking.
- Errors are captured and returned in AlertDeliveryResult — never raised.
"""

from __future__ import annotations

import asyncio
import smtplib
from email.mime.text import MIMEText

from app.alerts.base.interfaces import AlertDeliveryResult, AlertMessage, BaseAlertChannel
from app.alerts.formatters import (
    format_email_body,
    format_email_digest_body,
    format_email_digest_subject,
    format_email_subject,
)
from app.core.settings import AlertSettings


class EmailAlertChannel(BaseAlertChannel):
    """Sends alerts via SMTP email."""

    def __init__(self, settings: AlertSettings) -> None:
        self._settings = settings

    @property
    def channel_name(self) -> str:
        return "email"

    @property
    def is_enabled(self) -> bool:
        return (
            self._settings.email_enabled
            and bool(self._settings.email_host)
            and bool(self._settings.email_from)
            and bool(self._settings.email_to)
        )

    async def send(self, message: AlertMessage) -> AlertDeliveryResult:
        if self._settings.dry_run:
            return AlertDeliveryResult(
                channel=self.channel_name, success=True, message_id="dry_run"
            )
        subject = format_email_subject(message)
        body = format_email_body(message)
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._send_smtp, subject, body)

    async def send_digest(
        self, messages: list[AlertMessage], period: str
    ) -> AlertDeliveryResult:
        if self._settings.dry_run:
            return AlertDeliveryResult(
                channel=self.channel_name, success=True, message_id="dry_run"
            )
        subject = format_email_digest_subject(len(messages), period)
        body = format_email_digest_body(messages, period)
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._send_smtp, subject, body)

    def _send_smtp(self, subject: str, body: str) -> AlertDeliveryResult:
        try:
            msg = MIMEText(body, "plain", "utf-8")
            msg["Subject"] = subject
            msg["From"] = self._settings.email_from
            msg["To"] = self._settings.email_to

            with smtplib.SMTP(self._settings.email_host, self._settings.email_port) as server:
                server.ehlo()
                server.starttls()
                if self._settings.email_user and self._settings.email_password:
                    server.login(self._settings.email_user, self._settings.email_password)
                server.sendmail(
                    self._settings.email_from,
                    [self._settings.email_to],
                    msg.as_string(),
                )
            return AlertDeliveryResult(channel=self.channel_name, success=True)
        except Exception as exc:  # noqa: BLE001
            return AlertDeliveryResult(
                channel=self.channel_name,
                success=False,
                error=str(exc)[:200],
            )
