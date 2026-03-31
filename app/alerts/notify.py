"""Simple operator notification via Telegram.

Thin wrapper around TelegramAlertChannel for sending plain-text
status/health messages to the operator without building a full
AlertMessage.

Usage:
    ok = await send_operator_notification("Health check: 2 issues found")
"""

from __future__ import annotations

from app.alerts.channels.telegram import TelegramAlertChannel
from app.core.settings import AlertSettings


async def send_operator_notification(text: str) -> bool:
    """Send a plain text notification to the operator via Telegram.

    Returns True if sent successfully, False if disabled or failed.
    """
    settings = AlertSettings()
    channel = TelegramAlertChannel(settings)

    if not channel.is_enabled:
        return False

    result = await channel._post_message(text)
    return result.success
