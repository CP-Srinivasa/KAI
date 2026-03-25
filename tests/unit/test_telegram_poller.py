"""Tests for TelegramPoller — long-polling runner for TelegramOperatorBot."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.messaging.telegram_bot import TelegramOperatorBot, TelegramPoller


def _make_bot(*, configured: bool = True) -> TelegramOperatorBot:
    """Create a TelegramOperatorBot for testing."""
    return TelegramOperatorBot(
        bot_token="fake-token" if configured else "",
        admin_chat_ids=[12345] if configured else [],
        dry_run=True,
    )


class TestTelegramPollerLifecycle:
    """Tests for start/stop lifecycle."""

    def test_start_creates_task_when_configured(self) -> None:
        bot = _make_bot(configured=True)
        poller = TelegramPoller(bot)

        with patch.object(asyncio, "create_task", return_value=MagicMock()) as mock_task:
            poller.start()

        assert poller._running is True
        mock_task.assert_called_once()

    def test_start_skips_when_not_configured(self) -> None:
        bot = _make_bot(configured=False)
        poller = TelegramPoller(bot)

        with patch.object(asyncio, "create_task") as mock_task:
            poller.start()

        assert poller._running is False
        mock_task.assert_not_called()

    def test_stop_sets_running_false_and_cancels_task(self) -> None:
        bot = _make_bot()
        poller = TelegramPoller(bot)
        mock_task = MagicMock()
        mock_task.done.return_value = False
        poller._task = mock_task
        poller._running = True

        poller.stop()

        assert poller._running is False
        mock_task.cancel.assert_called_once()

    def test_stop_is_idempotent_without_task(self) -> None:
        bot = _make_bot()
        poller = TelegramPoller(bot)

        poller.stop()  # should not raise

        assert poller._running is False
        assert poller._task is None

    def test_stop_skips_cancel_when_task_already_done(self) -> None:
        bot = _make_bot()
        poller = TelegramPoller(bot)
        mock_task = MagicMock()
        mock_task.done.return_value = True
        poller._task = mock_task
        poller._running = True

        poller.stop()

        assert poller._running is False
        mock_task.cancel.assert_not_called()


class TestTelegramPollerPollLoop:
    """Tests for the async poll loop logic."""

    @pytest.mark.asyncio
    async def test_poll_loop_processes_updates(self) -> None:
        bot = _make_bot()
        bot.process_update = AsyncMock()
        poller = TelegramPoller(bot, poll_interval=0.01, long_poll_timeout=1)

        updates = [
            {"update_id": 100, "message": {"chat": {"id": 12345}, "text": "/status"}},
            {"update_id": 101, "message": {"chat": {"id": 12345}, "text": "/help"}},
        ]

        call_count = 0

        async def fake_get(url, params=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                resp = MagicMock()
                resp.json.return_value = {"result": updates}
                return resp
            # Second call: stop the loop
            poller._running = False
            resp = MagicMock()
            resp.json.return_value = {"result": []}
            return resp

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = fake_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            poller._running = True
            await poller._poll_loop()

        assert bot.process_update.call_count == 2
        assert poller._offset == 102  # last update_id + 1

    @pytest.mark.asyncio
    async def test_poll_loop_handles_update_processing_error(self) -> None:
        bot = _make_bot()
        bot.process_update = AsyncMock(side_effect=RuntimeError("handler crash"))
        poller = TelegramPoller(bot, poll_interval=0.01, long_poll_timeout=1)

        call_count = 0

        async def fake_get(url, params=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                resp = MagicMock()
                resp.json.return_value = {
                    "result": [
                        {"update_id": 200, "message": {"chat": {"id": 12345}, "text": "/status"}}
                    ]
                }
                return resp
            poller._running = False
            resp = MagicMock()
            resp.json.return_value = {"result": []}
            return resp

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = fake_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            poller._running = True
            await poller._poll_loop()  # should not raise

        # Error handled, offset still advanced
        assert poller._offset == 201

    @pytest.mark.asyncio
    async def test_poll_loop_exits_on_cancelled_error(self) -> None:
        bot = _make_bot()
        poller = TelegramPoller(bot, poll_interval=0.01, long_poll_timeout=1)

        async def fake_get(url, params=None):
            raise asyncio.CancelledError()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = fake_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            poller._running = True
            await poller._poll_loop()  # should exit cleanly

    @pytest.mark.asyncio
    async def test_poll_loop_continues_on_network_error(self) -> None:
        bot = _make_bot()
        poller = TelegramPoller(bot, poll_interval=0.01, long_poll_timeout=1)

        call_count = 0

        async def fake_get(url, params=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("network down")
            poller._running = False
            resp = MagicMock()
            resp.json.return_value = {"result": []}
            return resp

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = fake_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            poller._running = True
            await poller._poll_loop()  # should not raise, should retry

        assert call_count == 2  # first failed, second stopped loop


class TestTelegramPollerConfiguration:
    """Tests for poller configuration."""

    def test_default_configuration(self) -> None:
        bot = _make_bot()
        poller = TelegramPoller(bot)

        assert poller._poll_interval == 1.0
        assert poller._long_poll_timeout == 20
        assert poller._offset == 0
        assert poller._running is False
        assert poller._task is None

    def test_custom_configuration(self) -> None:
        bot = _make_bot()
        poller = TelegramPoller(bot, poll_interval=5.0, long_poll_timeout=30)

        assert poller._poll_interval == 5.0
        assert poller._long_poll_timeout == 30
