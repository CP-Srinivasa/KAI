"""
Telegram Alert Adapter
=======================
Sends alert messages to a Telegram chat via the Bot API.

[REQUIRES: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env]
[REQUIRES: TELEGRAM_ENABLED=true in .env]

Features:
- MarkdownV2 formatted messages
- Dry-run mode (logs instead of sending)
- Retry with exponential backoff
- Character limit enforcement (4096 chars)
- Breaking alert vs. digest formatting

Configuration:
    TELEGRAM_BOT_TOKEN=123456789:AAFabc...
    TELEGRAM_CHAT_ID=-1001234567890   (group) or 123456789 (user)
    TELEGRAM_ENABLED=true
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

import httpx
from tenacity import AsyncRetrying, stop_after_attempt, wait_exponential

from app.alerts.evaluator import AlertDecision, DocumentScores
from app.core.enums import AlertType, DocumentPriority
from app.core.errors import AppError
from app.core.logging import get_logger

logger = get_logger(__name__)

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
_MAX_MESSAGE_LENGTH = 4096
_SEND_TIMEOUT = 15.0

# Priority → emoji prefix
_PRIORITY_EMOJI = {
    DocumentPriority.CRITICAL: "🚨",
    DocumentPriority.HIGH:     "🔴",
    DocumentPriority.MEDIUM:   "🟡",
    DocumentPriority.LOW:      "🟢",
    DocumentPriority.NOISE:    "⚪",
}

_SENTIMENT_EMOJI = {
    "positive": "📈",
    "negative": "📉",
    "neutral":  "➡️",
}


# ─────────────────────────────────────────────
# MarkdownV2 escaping
# ─────────────────────────────────────────────

_MD_ESCAPE = re.compile(r"([_*\[\]()~`>#+\-=|{}.!\\])")


def escape_md(text: str) -> str:
    """Escape special characters for Telegram MarkdownV2."""
    return _MD_ESCAPE.sub(r"\\\1", str(text))


# ─────────────────────────────────────────────
# Message Formatters
# ─────────────────────────────────────────────

def format_breaking_alert(scores: DocumentScores) -> str:
    """
    Format a short, high-signal breaking alert message.
    Designed to be scannable in seconds.
    """
    priority_emoji = _PRIORITY_EMOJI.get(scores.recommended_priority, "⚪")
    sentiment_emoji = _SENTIMENT_EMOJI.get(scores.sentiment_label, "➡️")

    lines: list[str] = []
    lines.append(f"{priority_emoji} *BREAKING: {escape_md(scores.title[:120])}*")
    lines.append("")

    if scores.explanation_short:
        lines.append(f"_{escape_md(scores.explanation_short[:200])}_")
        lines.append("")

    # Key metrics
    metrics: list[str] = []
    metrics.append(f"{sentiment_emoji} Sentiment: `{escape_md(scores.sentiment_label)}`")
    if scores.impact_score > 0:
        metrics.append(f"💥 Impact: `{scores.impact_score:.0%}`")
    if scores.affected_assets:
        assets = ", ".join(scores.affected_assets[:5])
        metrics.append(f"🎯 Assets: `{escape_md(assets)}`")
    if scores.matched_entities:
        entities = ", ".join(scores.matched_entities[:3])
        metrics.append(f"👤 Entities: `{escape_md(entities)}`")
    lines.extend(metrics)

    # Source info
    lines.append("")
    if scores.url:
        lines.append(f"🔗 [Read more]({scores.url})")
    pub = scores.published_at
    if pub:
        lines.append(f"📅 {escape_md(pub.strftime('%Y-%m-%d %H:%M UTC'))}")

    return "\n".join(lines)[:_MAX_MESSAGE_LENGTH]


def format_watchlist_alert(scores: DocumentScores) -> str:
    """Format a watchlist hit alert — entity-focused."""
    lines: list[str] = []
    entities = ", ".join(scores.matched_entities[:5]) if scores.matched_entities else "—"
    lines.append(f"👁 *Watchlist Hit: {escape_md(entities)}*")
    lines.append("")
    lines.append(f"📰 {escape_md(scores.title[:150])}")
    if scores.explanation_short:
        lines.append(f"_{escape_md(scores.explanation_short[:180])}_")
    lines.append("")
    if scores.url:
        lines.append(f"🔗 [Read more]({scores.url})")
    return "\n".join(lines)[:_MAX_MESSAGE_LENGTH]


def format_digest_message(
    items: list[DocumentScores],
    period: str = "Daily",
) -> str:
    """
    Format a digest message with a ranked list of top documents.
    Used for scheduled digest alerts.
    """
    lines: list[str] = []
    now = datetime.utcnow().strftime("%Y-%m-%d")
    lines.append(f"📋 *{escape_md(period)} Digest — {escape_md(now)}*")
    lines.append(f"_{escape_md(str(len(items)))} documents analyzed_")
    lines.append("")

    for i, scores in enumerate(items[:15], 1):
        emoji = _PRIORITY_EMOJI.get(scores.recommended_priority, "⚪")
        sent_emoji = _SENTIMENT_EMOJI.get(scores.sentiment_label, "➡️")
        title_short = escape_md(scores.title[:80])
        lines.append(f"{i}\\. {emoji} {sent_emoji} *{title_short}*")
        if scores.explanation_short:
            lines.append(f"   _{escape_md(scores.explanation_short[:100])}_")
        if scores.url:
            lines.append(f"   🔗 [link]({scores.url})")
        lines.append("")

    return "\n".join(lines)[:_MAX_MESSAGE_LENGTH]


def format_alert_message(decision: AlertDecision) -> str:
    """Route to the correct formatter based on alert type."""
    scores = decision.document_scores
    if scores is None:
        return f"Alert triggered: {decision.rule_name}"

    if decision.alert_type == AlertType.WATCHLIST_HIT:
        return format_watchlist_alert(scores)
    return format_breaking_alert(scores)


# ─────────────────────────────────────────────
# Telegram Adapter
# ─────────────────────────────────────────────

class TelegramAdapter:
    """
    Sends messages to a Telegram chat.

    [REQUIRES: TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID + TELEGRAM_ENABLED=true]

    Args:
        bot_token:    Telegram Bot API token
        chat_id:      Target chat/channel/group ID
        dry_run:      If True, logs message instead of sending
        max_retries:  Retry count on transient failures
    """

    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        dry_run: bool = False,
        max_retries: int = 3,
    ) -> None:
        self._token = bot_token
        self._chat_id = chat_id
        self._dry_run = dry_run
        self._max_retries = max_retries
        self._url = _TELEGRAM_API.format(token=bot_token)

    async def send_text(self, text: str, parse_mode: str = "MarkdownV2") -> bool:
        """
        Send a raw text message. Returns True on success.
        In dry_run mode: logs the message, returns True without sending.
        """
        if self._dry_run:
            logger.info(
                "telegram_dry_run",
                chat_id=self._chat_id,
                message_length=len(text),
                preview=text[:100],
            )
            return True

        if not self._token or not self._chat_id:
            logger.warning("telegram_not_configured")
            return False

        payload = {
            "chat_id": self._chat_id,
            "text": text[:_MAX_MESSAGE_LENGTH],
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }

        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(self._max_retries),
                wait=wait_exponential(min=1.0, max=30.0),
                reraise=True,
            ):
                with attempt:
                    async with httpx.AsyncClient(timeout=_SEND_TIMEOUT) as client:
                        resp = await client.post(self._url, json=payload)
                    if resp.status_code == 429:
                        retry_after = int(resp.headers.get("Retry-After", 5))
                        logger.warning("telegram_rate_limited", retry_after=retry_after)
                        raise AppError(f"Telegram rate limited, retry after {retry_after}s")
                    resp.raise_for_status()
            logger.info("telegram_sent", chat_id=self._chat_id, length=len(text))
            return True
        except Exception as e:
            logger.error("telegram_send_failed", error=str(e), chat_id=self._chat_id)
            return False

    async def send_alert(self, decision: AlertDecision) -> bool:
        """Format and send an AlertDecision as a Telegram message."""
        message = format_alert_message(decision)
        return await self.send_text(message)

    async def send_digest(self, items: list[DocumentScores], period: str = "Daily") -> bool:
        """Format and send a digest message."""
        message = format_digest_message(items, period)
        return await self.send_text(message)

    async def healthcheck(self) -> dict[str, Any]:
        """Test connectivity by calling getMe."""
        if self._dry_run:
            return {"healthy": True, "mode": "dry_run"}
        if not self._token:
            return {"healthy": False, "reason": "TELEGRAM_BOT_TOKEN not set"}
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"https://api.telegram.org/bot{self._token}/getMe"
                )
            data = resp.json()
            return {
                "healthy": data.get("ok", False),
                "bot_username": data.get("result", {}).get("username"),
                "chat_id": self._chat_id,
            }
        except Exception as e:
            return {"healthy": False, "error": str(e)}
