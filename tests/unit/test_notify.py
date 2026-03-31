"""Tests for operator notification and ops-status."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.alerts.notify import send_operator_notification

# ── send_operator_notification ─────────────────────────────────────


def test_notify_disabled_when_telegram_not_configured() -> None:
    """Returns False when Telegram is not enabled."""
    with patch(
        "app.alerts.notify.AlertSettings",
    ) as mock_settings_cls:
        settings = mock_settings_cls.return_value
        settings.telegram_enabled = False
        settings.telegram_token = ""
        settings.telegram_chat_id = ""

        result = asyncio.run(send_operator_notification("test"))
        assert result is False


def test_notify_sends_when_configured() -> None:
    """Returns True when message is sent successfully."""
    with (
        patch("app.alerts.notify.AlertSettings") as mock_settings_cls,
        patch(
            "app.alerts.notify.TelegramAlertChannel",
        ) as mock_channel_cls,
    ):
        settings = mock_settings_cls.return_value
        settings.telegram_enabled = True
        settings.telegram_token = "tok"
        settings.telegram_chat_id = "123"

        channel = mock_channel_cls.return_value
        channel.is_enabled = True

        from app.alerts.base.interfaces import AlertDeliveryResult

        channel._post_message = AsyncMock(
            return_value=AlertDeliveryResult(
                channel="telegram", success=True, message_id="42",
            ),
        )

        result = asyncio.run(send_operator_notification("hello"))
        assert result is True
        channel._post_message.assert_awaited_once_with("hello")


def test_notify_returns_false_on_failure() -> None:
    """Returns False when send fails."""
    with (
        patch("app.alerts.notify.AlertSettings") as mock_settings_cls,
        patch(
            "app.alerts.notify.TelegramAlertChannel",
        ) as mock_channel_cls,
    ):
        settings = mock_settings_cls.return_value
        settings.telegram_enabled = True
        settings.telegram_token = "tok"
        settings.telegram_chat_id = "123"

        channel = mock_channel_cls.return_value
        channel.is_enabled = True

        from app.alerts.base.interfaces import AlertDeliveryResult

        channel._post_message = AsyncMock(
            return_value=AlertDeliveryResult(
                channel="telegram", success=False, error="timeout",
            ),
        )

        result = asyncio.run(send_operator_notification("hello"))
        assert result is False


# ── ops-status helpers ─────────────────────────────────────────────


def _write_audit(tmp_path: Path, **kwargs) -> None:
    from app.alerts.audit import (
        ALERT_AUDIT_JSONL_FILENAME,
        AlertAuditRecord,
        append_alert_audit,
    )

    defaults = {
        "document_id": "doc-1",
        "channel": "telegram",
        "message_id": "dry_run",
        "is_digest": False,
        "dispatched_at": datetime.now(UTC).isoformat(),
        "sentiment_label": "bullish",
        "affected_assets": ["BTC/USDT"],
        "directional_eligible": True,
    }
    defaults.update(kwargs)
    rec = AlertAuditRecord(**defaults)
    append_alert_audit(rec, tmp_path / ALERT_AUDIT_JSONL_FILENAME)


def _write_cycle(tmp_path: Path, **kwargs) -> None:
    defaults = {
        "cycle_id": "cyc_test",
        "started_at": datetime.now(UTC).isoformat(),
        "symbol": "BTC/USDT",
        "status": "completed",
        "fill_simulated": True,
    }
    defaults.update(kwargs)
    with (tmp_path / "trading_loop_audit.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(defaults) + "\n")


def test_ops_status_combines_health_and_briefing(tmp_path: Path) -> None:
    """ops-status produces output from both health check and briefing data."""
    from app.alerts.daily_briefing import build_daily_briefing
    from app.alerts.health_check import run_health_check

    _write_audit(tmp_path, document_id="d1")
    _write_cycle(tmp_path, cycle_id="c1")

    issues = run_health_check(
        tmp_path, min_expected_alerts=0, min_expected_cycles=0,
    )
    data = build_daily_briefing(tmp_path)

    assert issues == []
    assert data.alerts_dispatched == 1
    assert data.cycles_total == 1
