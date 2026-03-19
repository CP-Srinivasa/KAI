"""Telegram alert channel.

Sends alerts via Telegram Bot API (sendMessage endpoint).
- In dry_run mode: no real HTTP request is made.
- Uses httpx async for non-blocking delivery.
- Errors are captured and returned in AlertDeliveryResult — never raised.
"""

from __future__ import annotations

import httpx

from app.alerts.base.interfaces import AlertDeliveryResult, AlertMessage, BaseAlertChannel
from app.alerts.formatters import format_telegram_digest, format_telegram_message
from app.core.settings import AlertSettings

_TELEGRAM_API_BASE = "https://api.telegram.org"


class TelegramAlertChannel(BaseAlertChannel):
    """Sends alerts via Telegram Bot API."""

    def __init__(self, settings: AlertSettings) -> None:
        self._settings = settings

    @property
    def channel_name(self) -> str:
        return "telegram"

    @property
    def is_enabled(self) -> bool:
        return (
            self._settings.telegram_enabled
            and bool(self._settings.telegram_token)
            and bool(self._settings.telegram_chat_id)
        )

    async def send(self, message: AlertMessage) -> AlertDeliveryResult:
        if self._settings.dry_run:
            return AlertDeliveryResult(
                channel=self.channel_name, success=True, message_id="dry_run"
            )
        text = format_telegram_message(message)
        return await self._post_message(text)

    async def send_digest(self, messages: list[AlertMessage], period: str) -> AlertDeliveryResult:
        if self._settings.dry_run:
            return AlertDeliveryResult(
                channel=self.channel_name, success=True, message_id="dry_run"
            )
        text = format_telegram_digest(messages, period)
        return await self._post_message(text)

    async def _post_message(self, text: str) -> AlertDeliveryResult:
        url = f"{_TELEGRAM_API_BASE}/bot{self._settings.telegram_token}/sendMessage"
        payload = {
            "chat_id": self._settings.telegram_chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.post(url, json=payload)
            response.raise_for_status()
            data: dict = response.json()
            message_id = str(data.get("result", {}).get("message_id", ""))
            return AlertDeliveryResult(
                channel=self.channel_name,
                success=True,
                message_id=message_id,
            )
        except httpx.HTTPStatusError as exc:
            return AlertDeliveryResult(
                channel=self.channel_name,
                success=False,
                error=f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
            )
        except Exception as exc:  # noqa: BLE001
            return AlertDeliveryResult(
                channel=self.channel_name,
                success=False,
                error=str(exc)[:200],
            )
