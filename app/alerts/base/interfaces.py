"""Alert system base interfaces.

AlertMessage        — the data payload delivered to all channels.
AlertDeliveryResult — result of one send attempt.
BaseAlertChannel    — ABC that every channel must implement.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass(frozen=True)
class AlertMessage:
    """Normalized representation of an alert to be sent via any channel."""

    document_id: str
    title: str
    url: str
    priority: int  # 1–10
    sentiment_label: str  # "bullish" | "bearish" | "neutral" | "mixed"
    actionable: bool
    explanation: str
    affected_assets: list[str] = field(default_factory=list)
    published_at: datetime | None = None
    source_name: str | None = None
    tags: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class AlertDeliveryResult:
    """Result of a single alert delivery attempt."""

    channel: str          # "telegram" | "email" | "dry_run"
    success: bool
    message_id: str | None = None   # channel-specific ID when available
    error: str | None = None


class BaseAlertChannel(ABC):
    """Abstract base for all alert delivery channels.

    Contract:
    - channel_name:  stable string identifier used in logs + results
    - is_enabled:    False means the channel is not configured / should be skipped
    - send():        deliver a single AlertMessage
    - send_digest(): deliver a bundled list of AlertMessages
    """

    @property
    @abstractmethod
    def channel_name(self) -> str:
        """Stable identifier, e.g. 'telegram' or 'email'."""
        ...

    @property
    @abstractmethod
    def is_enabled(self) -> bool:
        """Whether this channel is configured and active."""
        ...

    @abstractmethod
    async def send(self, message: AlertMessage) -> AlertDeliveryResult:
        """Send a single alert message."""
        ...

    @abstractmethod
    async def send_digest(
        self, messages: list[AlertMessage], period: str
    ) -> AlertDeliveryResult:
        """Send a bundled digest of multiple alert messages.

        period: human-readable label e.g. "last 60 minutes"
        """
        ...
