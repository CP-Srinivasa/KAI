"""Telegram alert channel.

Sends alerts via Telegram Bot API (sendMessage endpoint).
- In dry_run mode: no real HTTP request is made.
- Uses httpx async for non-blocking delivery.
- Errors are captured and returned in AlertDeliveryResult — never raised.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.alerts.base.interfaces import AlertDeliveryResult, AlertMessage, BaseAlertChannel
from app.alerts.formatters import format_telegram_digest, format_telegram_message
from app.core.settings import AlertSettings

_TELEGRAM_API_BASE = "https://api.telegram.org"
_TELEGRAM_MAX_TEXT_LEN = 4096
_TELEGRAM_MAX_RETRIES = 3
_TELEGRAM_MAX_RETRY_SLEEP_SECONDS = 5


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

    async def _post_message(
        self, text: str, *, parse_mode: str | None = "Markdown",
    ) -> AlertDeliveryResult:
        url = f"{_TELEGRAM_API_BASE}/bot{self._settings.telegram_token}/sendMessage"
        chunks = _split_telegram_text(text)
        last_message_id = ""
        for idx, chunk in enumerate(chunks, start=1):
            payload: dict[str, str | bool] = {
                "chat_id": self._settings.telegram_chat_id,
                "text": chunk,
                "disable_web_page_preview": True,
            }
            if parse_mode is not None:
                payload["parse_mode"] = parse_mode
            result = await self._post_payload_with_retry(url, payload)
            if not result.success:
                return AlertDeliveryResult(
                    channel=self.channel_name,
                    success=False,
                    error=f"chunk {idx}/{len(chunks)} failed: {result.error}",
                )
            last_message_id = result.message_id or last_message_id

        return AlertDeliveryResult(
            channel=self.channel_name,
            success=True,
            message_id=last_message_id,
        )

    async def _post_payload_with_retry(
        self,
        url: str,
        payload: dict[str, str | bool],
    ) -> AlertDeliveryResult:
        for attempt in range(1, _TELEGRAM_MAX_RETRIES + 1):
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    response = await client.post(url, json=payload)

                if response.status_code == 429 and attempt < _TELEGRAM_MAX_RETRIES:
                    retry_after = _extract_retry_after_seconds(response)
                    await asyncio.sleep(min(retry_after, _TELEGRAM_MAX_RETRY_SLEEP_SECONDS))
                    continue

                response.raise_for_status()
                data: dict[str, Any] = response.json()
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
                if attempt < _TELEGRAM_MAX_RETRIES:
                    await asyncio.sleep(1)
                    continue
                return AlertDeliveryResult(
                    channel=self.channel_name,
                    success=False,
                    error=str(exc)[:200],
                )

        return AlertDeliveryResult(
            channel=self.channel_name,
            success=False,
            error="unknown_telegram_delivery_error",
        )


def _split_telegram_text(text: str) -> list[str]:
    if len(text) <= _TELEGRAM_MAX_TEXT_LEN:
        return [text]
    chunks: list[str] = []
    rest = text
    while len(rest) > _TELEGRAM_MAX_TEXT_LEN:
        cut = rest.rfind("\n", 0, _TELEGRAM_MAX_TEXT_LEN)
        if cut < int(_TELEGRAM_MAX_TEXT_LEN * 0.6):
            cut = _TELEGRAM_MAX_TEXT_LEN
        chunks.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip("\n")
    if rest:
        chunks.append(rest)
    return chunks


def _extract_retry_after_seconds(response: httpx.Response) -> int:
    try:
        payload: dict[str, Any] = response.json()
    except Exception:  # noqa: BLE001
        return 1
    params = payload.get("parameters")
    if isinstance(params, dict):
        retry_after = params.get("retry_after")
        if isinstance(retry_after, int) and retry_after > 0:
            return retry_after
    return 1
