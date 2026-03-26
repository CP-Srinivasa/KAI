"""Unit tests for TelegramOperatorBot."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.cli.commands.trading import get_invalid_trading_command_refs
from app.messaging.telegram_bot import (
    TelegramOperatorBot,
    get_telegram_command_inventory,
)
from app.messaging.text_intent import IntentResult
from app.risk.engine import RiskEngine
from app.risk.models import RiskLimits


def _limits() -> RiskLimits:
    return RiskLimits(
        initial_equity=10000.0,
        max_risk_per_trade_pct=0.25,
        max_daily_loss_pct=1.0,
        max_total_drawdown_pct=5.0,
        max_open_positions=3,
        max_leverage=1.0,
        require_stop_loss=True,
        allow_averaging_down=False,
        allow_martingale=False,
        kill_switch_enabled=True,
        min_signal_confidence=0.75,
        min_signal_confluence_count=2,
    )


def _bot(tmp_path, risk_engine=None, **kwargs: Any) -> TelegramOperatorBot:
    kwargs.setdefault("signal_handoff_log_path", str(tmp_path / "signal_handoff.jsonl"))
    kwargs.setdefault("signal_exchange_outbox_log_path", str(tmp_path / "exchange_outbox.jsonl"))
    kwargs.setdefault("signal_exchange_sent_log_path", str(tmp_path / "exchange_sent.jsonl"))
    kwargs.setdefault(
        "signal_exchange_dead_letter_log_path",
        str(tmp_path / "exchange_dead_letter.jsonl"),
    )
    return TelegramOperatorBot(
        bot_token="fake_token",
        admin_chat_ids=[12345],
        audit_log_path=str(tmp_path / "cmd_audit.jsonl"),
        risk_engine=risk_engine,
        dry_run=True,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_unauthorized_chat_id_is_rejected_and_not_audited(tmp_path, monkeypatch):
    bot = _bot(tmp_path)
    sent_messages: list[str] = []

    async def fake_send(_chat_id: int, text: str) -> bool:
        sent_messages.append(text)
        return True

    monkeypatch.setattr(bot, "_send", fake_send)

    update = {"message": {"chat": {"id": 99999}, "text": "/status"}}
    await bot.process_update(update)

    assert len(sent_messages) == 1
    assert "Unauthorized" in sent_messages[0]
    assert not (tmp_path / "cmd_audit.jsonl").exists()


@pytest.mark.asyncio
async def test_kill_requires_confirmation(tmp_path):
    bot = _bot(tmp_path)
    update = {"message": {"chat": {"id": 12345}, "text": "/kill"}}
    await bot.process_update(update)
    # After first /kill, pending_confirm should be set
    assert bot._pending_confirm.get(12345) == "kill"


@pytest.mark.asyncio
async def test_double_kill_activates_in_dry_run(tmp_path):
    re = RiskEngine(_limits())
    bot = _bot(tmp_path, risk_engine=re)
    update = {"message": {"chat": {"id": 12345}, "text": "/kill"}}
    await bot.process_update(update)
    await bot.process_update(update)  # second kill confirms
    # In dry_run mode, kill switch should NOT be triggered (dry_run protects)
    assert not re._kill_switch_active


@pytest.mark.asyncio
async def test_pause_in_dry_run(tmp_path):
    re = RiskEngine(_limits())
    bot = _bot(tmp_path, risk_engine=re)
    update = {"message": {"chat": {"id": 12345}, "text": "/pause"}}
    await bot.process_update(update)
    # dry_run: system not actually paused
    assert not re._paused


@pytest.mark.asyncio
async def test_resume_in_dry_run(tmp_path):
    re = RiskEngine(_limits())
    bot = _bot(tmp_path, risk_engine=re)
    update = {"message": {"chat": {"id": 12345}, "text": "/resume"}}
    await bot.process_update(update)
    assert bot._system_status == "operational"
    assert not re._paused


@pytest.mark.asyncio
async def test_audit_log_written(tmp_path):
    bot = _bot(tmp_path)
    update = {"message": {"chat": {"id": 12345}, "text": "/status"}}
    await bot.process_update(update)

    audit_file = tmp_path / "cmd_audit.jsonl"
    assert audit_file.exists()
    lines = audit_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) >= 1
    record = json.loads(lines[0])
    assert record["command"] == "status"
    assert record["chat_id"] == 12345


@pytest.mark.asyncio
async def test_unknown_command(tmp_path):
    bot = _bot(tmp_path)
    update = {"message": {"chat": {"id": 12345}, "text": "/nonexistent"}}
    await bot.process_update(update)  # should not raise


@pytest.mark.asyncio
async def test_menu_command_routes_to_main_menu(tmp_path, monkeypatch):
    bot = _bot(tmp_path)
    calls: list[tuple[int, str, int | None]] = []

    async def fake_send_menu(chat_id: int, menu_id: str, *, message_id: int | None = None) -> bool:
        calls.append((chat_id, menu_id, message_id))
        return True

    monkeypatch.setattr(bot, "_send_menu", fake_send_menu)

    await bot.process_update({"message": {"chat": {"id": 12345}, "text": "/menu"}})

    assert calls == [(12345, "main", None)]


@pytest.mark.asyncio
async def test_menu_alias_menue_routes_to_main_menu(tmp_path, monkeypatch):
    bot = _bot(tmp_path)
    calls: list[tuple[int, str, int | None]] = []

    async def fake_send_menu(chat_id: int, menu_id: str, *, message_id: int | None = None) -> bool:
        calls.append((chat_id, menu_id, message_id))
        return True

    monkeypatch.setattr(bot, "_send_menu", fake_send_menu)

    await bot.process_update({"message": {"chat": {"id": 12345}, "text": "/menue"}})

    assert calls == [(12345, "main", None)]


@pytest.mark.asyncio
async def test_menu_reload_command_clears_cache_and_confirms(tmp_path, monkeypatch):
    bot = _bot(tmp_path)
    sent_messages: list[str] = []
    clear_calls = 0

    def fake_clear_menu_cache() -> None:
        nonlocal clear_calls
        clear_calls += 1

    async def fake_send(_chat_id: int, text: str) -> bool:
        sent_messages.append(text)
        return True

    monkeypatch.setattr("app.messaging.telegram_menu.clear_menu_cache", fake_clear_menu_cache)
    monkeypatch.setattr(bot, "_send", fake_send)

    await bot.process_update({"message": {"chat": {"id": 12345}, "text": "/menu_reload"}})

    assert clear_calls == 1
    assert len(sent_messages) == 1
    assert "Menue neu geladen" in sent_messages[0]


@pytest.mark.asyncio
async def test_menu_reload_alias_menue_reload_clears_cache(tmp_path, monkeypatch):
    bot = _bot(tmp_path)
    clear_calls = 0

    def fake_clear_menu_cache() -> None:
        nonlocal clear_calls
        clear_calls += 1

    async def fake_send(_chat_id: int, _text: str) -> bool:
        return True

    monkeypatch.setattr("app.messaging.telegram_menu.clear_menu_cache", fake_clear_menu_cache)
    monkeypatch.setattr(bot, "_send", fake_send)

    await bot.process_update({"message": {"chat": {"id": 12345}, "text": "/menue_reload"}})

    assert clear_calls == 1


@pytest.mark.asyncio
async def test_menu_validate_command_reports_status(tmp_path, monkeypatch):
    bot = _bot(tmp_path)
    sent_messages: list[str] = []

    def fake_validate_menu_config() -> dict[str, object]:
        return {
            "path": "config/telegram_menu.json",
            "source": "json",
            "is_valid": True,
            "menu_count": 5,
            "warning_count": 0,
            "error_count": 0,
            "warnings": [],
            "errors": [],
        }

    async def fake_send(_chat_id: int, text: str) -> bool:
        sent_messages.append(text)
        return True

    monkeypatch.setattr(
        "app.messaging.telegram_menu.validate_menu_config",
        fake_validate_menu_config,
    )
    monkeypatch.setattr(bot, "_send", fake_send)

    await bot.process_update({"message": {"chat": {"id": 12345}, "text": "/menu_validate"}})

    assert len(sent_messages) == 1
    assert "Menue-Validierung" in sent_messages[0]
    assert "Status: `OK`" in sent_messages[0]
    assert "Menues: `5`" in sent_messages[0]


@pytest.mark.asyncio
async def test_menu_validate_alias_menue_validate_routes_to_validator(tmp_path, monkeypatch):
    bot = _bot(tmp_path)
    validate_calls = 0

    def fake_validate_menu_config() -> dict[str, object]:
        nonlocal validate_calls
        validate_calls += 1
        return {
            "path": "config/telegram_menu.json",
            "source": "json",
            "is_valid": False,
            "menu_count": 0,
            "warning_count": 0,
            "error_count": 1,
            "warnings": [],
            "errors": ["menu_config_read_failed"],
        }

    async def fake_send(_chat_id: int, _text: str) -> bool:
        return True

    monkeypatch.setattr(
        "app.messaging.telegram_menu.validate_menu_config",
        fake_validate_menu_config,
    )
    monkeypatch.setattr(bot, "_send", fake_send)

    await bot.process_update({"message": {"chat": {"id": 12345}, "text": "/menue_validate"}})

    assert validate_calls == 1


@pytest.mark.asyncio
async def test_callback_menu_navigation_edits_existing_menu(tmp_path, monkeypatch):
    bot = _bot(tmp_path)
    menu_calls: list[tuple[int, str, int | None]] = []
    ack_calls: list[tuple[str, str | None]] = []

    async def fake_send_menu(chat_id: int, menu_id: str, *, message_id: int | None = None) -> bool:
        menu_calls.append((chat_id, menu_id, message_id))
        return True

    async def fake_answer_callback_query(
        callback_query_id: str, *, text: str | None = None
    ) -> bool:
        ack_calls.append((callback_query_id, text))
        return True

    monkeypatch.setattr(bot, "_send_menu", fake_send_menu)
    monkeypatch.setattr(bot, "_answer_callback_query", fake_answer_callback_query)

    await bot.process_update(
        {
            "callback_query": {
                "id": "cbq-menu-1",
                "from": {"id": 12345},
                "data": "menu:signals",
                "message": {"message_id": 77},
            }
        }
    )

    assert menu_calls == [(12345, "signals", 77)]
    assert ack_calls == [("cbq-menu-1", None)]


@pytest.mark.asyncio
async def test_callback_command_dispatches_and_acks(tmp_path, monkeypatch):
    bot = _bot(tmp_path)
    dispatch_calls: list[tuple[int, str, str]] = []
    ack_calls: list[tuple[str, str | None]] = []

    async def fake_dispatch(chat_id: int, command: str, *, args: str = "") -> None:
        dispatch_calls.append((chat_id, command, args))

    async def fake_answer_callback_query(
        callback_query_id: str, *, text: str | None = None
    ) -> bool:
        ack_calls.append((callback_query_id, text))
        return True

    monkeypatch.setattr(bot, "_dispatch", fake_dispatch)
    monkeypatch.setattr(bot, "_answer_callback_query", fake_answer_callback_query)

    await bot.process_update(
        {
            "callback_query": {
                "id": "cbq-cmd-1",
                "from": {"id": 12345},
                "data": "cmd:status",
                "message": {"message_id": 88},
            }
        }
    )

    assert dispatch_calls == [(12345, "status", "")]
    assert ack_calls == [("cbq-cmd-1", None)]


@pytest.mark.asyncio
async def test_callback_query_rejects_unauthorized_user(tmp_path, monkeypatch):
    bot = _bot(tmp_path)
    ack_calls: list[tuple[str, str | None]] = []
    dispatch_calls: list[tuple[int, str]] = []

    async def fake_answer_callback_query(
        callback_query_id: str, *, text: str | None = None
    ) -> bool:
        ack_calls.append((callback_query_id, text))
        return True

    async def fake_dispatch(chat_id: int, command: str, *, args: str = "") -> None:
        dispatch_calls.append((chat_id, command))

    monkeypatch.setattr(bot, "_answer_callback_query", fake_answer_callback_query)
    monkeypatch.setattr(bot, "_dispatch", fake_dispatch)

    await bot.process_update(
        {
            "callback_query": {
                "id": "cbq-unauth-1",
                "from": {"id": 99999},
                "data": "cmd:status",
                "message": {"message_id": 99},
            }
        }
    )

    assert ack_calls == [("cbq-unauth-1", "Nicht autorisiert.")]
    assert dispatch_calls == []


@pytest.mark.asyncio
async def test_is_not_configured_without_token():
    bot = TelegramOperatorBot(
        bot_token="",
        admin_chat_ids=[],
        dry_run=True,
    )
    assert not bot.is_configured


@pytest.mark.asyncio
async def test_send_splits_long_operator_message(tmp_path, monkeypatch):
    bot = TelegramOperatorBot(
        bot_token="token",
        admin_chat_ids=[12345],
        dry_run=False,
        audit_log_path=str(tmp_path / "cmd_audit.jsonl"),
    )
    posted_texts: list[str] = []
    request = httpx.Request("POST", "https://api.telegram.org")
    response = httpx.Response(200, json={"ok": True}, request=request)

    class _FakeClient:
        def __init__(self, *, timeout: int):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, _url: str, json: dict[str, object]):
            posted_texts.append(str(json["text"]))
            return response

    monkeypatch.setattr("app.messaging.telegram_bot.httpx.AsyncClient", _FakeClient)

    ok = await bot._send(12345, "A" * 5000)

    assert ok is True
    assert len(posted_texts) >= 2
    assert all(len(chunk) <= 4096 for chunk in posted_texts)


@pytest.mark.asyncio
async def test_send_retries_on_429(tmp_path, monkeypatch):
    bot = TelegramOperatorBot(
        bot_token="token",
        admin_chat_ids=[12345],
        dry_run=False,
        audit_log_path=str(tmp_path / "cmd_audit.jsonl"),
    )
    request = httpx.Request("POST", "https://api.telegram.org")
    responses = [
        httpx.Response(
            429,
            json={"ok": False, "parameters": {"retry_after": 1}},
            request=request,
        ),
        httpx.Response(200, json={"ok": True}, request=request),
    ]
    sleeps: list[float] = []

    class _FakeClient:
        def __init__(self, *, timeout: int):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, _url: str, json: dict[str, object]):
            return responses.pop(0)

    async def _fake_sleep(seconds: float):
        sleeps.append(seconds)

    monkeypatch.setattr("app.messaging.telegram_bot.httpx.AsyncClient", _FakeClient)
    monkeypatch.setattr("app.messaging.telegram_bot.asyncio.sleep", _fake_sleep)

    ok = await bot._send(12345, "test")

    assert ok is True
    assert sleeps == [1]


def test_telegram_command_inventory_references_registered_cli_trading_commands() -> None:
    inventory = get_telegram_command_inventory()
    refs = [
        ref
        for command_refs in inventory["canonical_command_refs"].values()
        for ref in command_refs
    ]
    assert get_invalid_trading_command_refs(refs) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("command_text", "loader_name", "payload", "expected_fragments"),
    [
        (
            "/status",
            "_get_daily_operator_summary",
            {
                "readiness_status": "warning",
                "cycle_count_today": 2,
                "position_count": 1,
                "execution_enabled": False,
                "write_back_allowed": False,
            },
            [
                "*KAI Status*",
                "Readiness: warning",
                "Zyklen heute: 2",
            ],
        ),
        (
            "/positions",
            "_get_paper_positions_summary",
            {
                "position_count": 2,
                "mark_to_market_status": "ok",
                "positions": [{"symbol": "BTC/USDT", "quantity": 0.5, "avg_entry_price": 65000}],
                "available": True,
                "execution_enabled": False,
                "write_back_allowed": False,
            },
            [
                "*Positions*",
                "Anzahl: 2",
                "BTC/USDT",
            ],
        ),
        (
            "/exposure",
            "_get_paper_exposure_summary",
            {
                "mark_to_market_status": "degraded",
                "gross_exposure_usd": 12000.0,
                "net_exposure_usd": 12000.0,
                "stale_position_count": 1,
                "unavailable_price_count": 0,
                "execution_enabled": False,
                "write_back_allowed": False,
            },
            [
                "*Exposure*",
                "Brutto: 12000.00 USD",
                "Stale: 1",
            ],
        ),
        (
            "/signals",
            "_get_signals_for_execution",
            {
                "signal_count": 1,
                "signals": [
                    {
                        "target_asset": "BTC",
                        "direction_hint": "bullish",
                        "priority": 9,
                    }
                ],
                "execution_enabled": False,
                "write_back_allowed": False,
            },
            [
                "*Signale*",
                "Anzahl: 1",
                "BTC",
                "bullish",
            ],
        ),
        (
            "/daily_summary",
            "_get_daily_operator_summary",
            {
                "readiness_status": "warning",
                "cycle_count_today": 2,
                "position_count": 1,
                "total_exposure_pct": 18.5,
                "decision_pack_status": "warning",
                "open_incidents": 1,
                "execution_enabled": False,
                "write_back_allowed": False,
            },
            [
                "*Tagesbericht*",
                "Readiness: warning",
                "Offene Vorfaelle: 1",
            ],
        ),
    ],
)
async def test_read_command_mapping_uses_canonical_surfaces(
    tmp_path,
    monkeypatch,
    command_text: str,
    loader_name: str,
    payload: dict[str, Any],
    expected_fragments: list[str],
):
    bot = _bot(tmp_path)
    sent_messages: list[str] = []
    called: list[str] = []

    async def fake_send(_chat_id: int, text: str) -> bool:
        sent_messages.append(text)
        return True

    async def fake_loader() -> dict[str, Any]:
        called.append(loader_name)
        return payload

    monkeypatch.setattr(bot, "_send", fake_send)
    monkeypatch.setattr(bot, loader_name, fake_loader)

    await bot.process_update({"message": {"chat": {"id": 12345}, "text": command_text}})

    assert called == [loader_name]
    assert len(sent_messages) == 1
    text = sent_messages[0]
    for fragment in expected_fragments:
        assert fragment in text
    assert " buy " not in text.lower()
    assert " sell " not in text.lower()


@pytest.mark.asyncio
async def test_read_command_fail_closed_on_surface_error(tmp_path, monkeypatch):
    bot = _bot(tmp_path)
    sent_messages: list[str] = []

    async def fake_send(_chat_id: int, text: str) -> bool:
        sent_messages.append(text)
        return True

    async def failing_loader() -> dict[str, Any]:
        raise RuntimeError("surface unavailable")

    monkeypatch.setattr(bot, "_send", fake_send)
    monkeypatch.setattr(bot, "_get_daily_operator_summary", failing_loader)

    await bot.process_update({"message": {"chat": {"id": 12345}, "text": "/status"}})

    assert len(sent_messages) == 1
    assert "fail-closed" in sent_messages[0].lower()
    assert "No execution side effect was performed." in sent_messages[0]


@pytest.mark.asyncio
async def test_alert_status_command_returns_read_only_payload(tmp_path, monkeypatch):
    bot = _bot(tmp_path)
    sent_messages: list[str] = []

    async def fake_send(_chat_id: int, text: str) -> bool:
        sent_messages.append(text)
        return True

    async def fake_loader() -> dict[str, Any]:
        return {
            "report_type": "alert_audit_summary",
            "total_count": 3,
            "digest_count": 1,
            "by_channel": {"telegram": 3},
            "latest_dispatched_at": "2026-03-22T10:00:00+00:00",
            "execution_enabled": False,
            "write_back_allowed": False,
        }

    monkeypatch.setattr(bot, "_send", fake_send)
    monkeypatch.setattr(bot, "_get_alert_audit_summary", fake_loader)

    await bot.process_update({"message": {"chat": {"id": 12345}, "text": "/alert_status"}})

    assert len(sent_messages) == 1
    assert "Alert Status" in sent_messages[0]
    assert "Gesamt: 3" in sent_messages[0]


@pytest.mark.asyncio
async def test_alert_status_command_degrades_on_loader_error(tmp_path, monkeypatch):
    bot = _bot(tmp_path)
    sent_messages: list[str] = []

    async def fake_send(_chat_id: int, text: str) -> bool:
        sent_messages.append(text)
        return True

    async def failing_loader() -> dict[str, Any]:
        raise RuntimeError("alert audit unavailable")

    monkeypatch.setattr(bot, "_send", fake_send)
    monkeypatch.setattr(bot, "_get_alert_audit_summary", failing_loader)

    await bot.process_update({"message": {"chat": {"id": 12345}, "text": "/alert_status"}})

    assert len(sent_messages) == 1
    assert "Alert Status" in sent_messages[0]
    assert "fail-closed" in sent_messages[0].lower()
    assert "No execution side effect was performed." in sent_messages[0]


@pytest.mark.asyncio
async def test_signal_status_command_returns_read_only_payload(tmp_path, monkeypatch):
    bot = _bot(tmp_path)
    sent_messages: list[str] = []

    async def fake_send(_chat_id: int, text: str) -> bool:
        sent_messages.append(text)
        return True

    def fake_build_signal_pipeline_status(**_kwargs: Any) -> dict[str, Any]:
        return {
            "report_type": "telegram_signal_pipeline_status",
            "lookback_hours": 24,
            "handoff_total": 7,
            "handoff_lookback": 2,
            "outbox_queued_total": 3,
            "exchange_sent_total": 4,
            "exchange_sent_lookback": 1,
            "exchange_dead_letter_total": 1,
            "exchange_dead_letter_lookback": 0,
            "execution_enabled": False,
            "write_back_allowed": False,
        }

    monkeypatch.setattr(bot, "_send", fake_send)
    monkeypatch.setattr(
        "app.messaging.exchange_relay.build_signal_pipeline_status",
        fake_build_signal_pipeline_status,
    )

    await bot.process_update({"message": {"chat": {"id": 12345}, "text": "/signal_status"}})

    assert len(sent_messages) == 1
    assert "Signal Status" in sent_messages[0]
    assert "Handoff: 7 gesamt" in sent_messages[0]
    assert "Outbox: 3 wartend" in sent_messages[0]


@pytest.mark.asyncio
async def test_read_command_fail_closed_on_invalid_payload_shape(tmp_path, monkeypatch):
    bot = _bot(tmp_path)
    sent_messages: list[str] = []

    async def fake_send(_chat_id: int, text: str) -> bool:
        sent_messages.append(text)
        return True

    async def invalid_loader() -> list[object]:
        return ["invalid", "payload"]

    monkeypatch.setattr(bot, "_send", fake_send)
    monkeypatch.setattr(bot, "_get_paper_positions_summary", invalid_loader)

    await bot.process_update({"message": {"chat": {"id": 12345}, "text": "/positions"}})

    assert len(sent_messages) == 1
    assert "Invalid canonical payload (fail-closed)." in sent_messages[0]
    assert "No execution side effect was performed." in sent_messages[0]


@pytest.mark.asyncio
async def test_read_command_fail_closed_when_command_refs_invalid(tmp_path, monkeypatch):
    bot = _bot(tmp_path)
    bot._invalid_command_refs = ("research missing-command",)
    sent_messages: list[str] = []

    async def fake_send(_chat_id: int, text: str) -> bool:
        sent_messages.append(text)
        return True

    async def should_not_run() -> dict[str, Any]:
        raise AssertionError("read surface must not run when refs are invalid")

    monkeypatch.setattr(bot, "_send", fake_send)
    monkeypatch.setattr(bot, "_get_daily_operator_summary", should_not_run)

    await bot.process_update({"message": {"chat": {"id": 12345}, "text": "/status"}})

    assert len(sent_messages) == 1
    assert "misconfigured" in sent_messages[0].lower()
    assert "fail-closed" in sent_messages[0].lower()


@pytest.mark.asyncio
async def test_approve_and_reject_are_guarded_audit_only(tmp_path, monkeypatch):
    bot = _bot(tmp_path)
    sent_messages: list[str] = []

    async def fake_send(_chat_id: int, text: str) -> bool:
        sent_messages.append(text)
        return True

    monkeypatch.setattr(bot, "_send", fake_send)

    await bot.process_update(
        {"message": {"chat": {"id": 12345}, "text": "/approve dec_abcdef123456"}}
    )
    await bot.process_update(
        {"message": {"chat": {"id": 12345}, "text": "/reject dec_123456abcdef"}}
    )

    audit_file = tmp_path / "cmd_audit.jsonl"
    lines = [json.loads(line) for line in audit_file.read_text(encoding="utf-8").splitlines()]

    assert lines[-2]["command"] == "approve"
    assert lines[-2]["args"] == "dec_abcdef123456"
    assert lines[-1]["command"] == "reject"
    assert lines[-1]["args"] == "dec_123456abcdef"
    assert "Audit-only. No execution side effect occurs." in sent_messages[0]
    assert "Audit-only. No execution side effect occurs." in sent_messages[1]
    assert sorted(path.name for path in tmp_path.iterdir()) == ["cmd_audit.jsonl"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("command_text", "expected_command"),
    [
        ("/approve", "approve"),
        ("/approve not_a_decision_id", "approve"),
        ("/reject", "reject"),
        ("/reject DEC_ABCDEF123456", "reject"),
    ],
)
async def test_approve_reject_fail_closed_on_invalid_decision_ref(
    tmp_path,
    monkeypatch,
    command_text: str,
    expected_command: str,
):
    bot = _bot(tmp_path)
    sent_messages: list[str] = []

    async def fake_send(_chat_id: int, text: str) -> bool:
        sent_messages.append(text)
        return True

    monkeypatch.setattr(bot, "_send", fake_send)

    await bot.process_update({"message": {"chat": {"id": 12345}, "text": command_text}})

    assert len(sent_messages) == 1
    assert "Invalid or missing `decision_ref`." in sent_messages[0]
    assert "Fail-closed" in sent_messages[0]
    assert "Audit-only. No execution side effect occurs." in sent_messages[0]

    audit_file = tmp_path / "cmd_audit.jsonl"
    lines = [json.loads(line) for line in audit_file.read_text(encoding="utf-8").splitlines()]
    assert lines[-1]["command"] == expected_command
    assert sorted(path.name for path in tmp_path.iterdir()) == ["cmd_audit.jsonl"]


@pytest.mark.asyncio
async def test_kill_confirmation_is_consumed_after_second_call(tmp_path):
    bot = _bot(tmp_path)
    update = {"message": {"chat": {"id": 12345}, "text": "/kill"}}
    await bot.process_update(update)
    assert bot._pending_confirm.get(12345) == "kill"

    await bot.process_update(update)
    assert 12345 not in bot._pending_confirm
    assert bot._system_status == "operational"


def test_validate_decision_ref_accepts_only_canonical_pattern(tmp_path) -> None:
    bot = _bot(tmp_path)
    assert bot._validate_decision_ref("dec_123456abcdef") == "dec_123456abcdef"
    assert bot._validate_decision_ref("dec_12345") is None
    assert bot._validate_decision_ref("DEC_123456abcdef") is None
    assert bot._validate_decision_ref("decision:123") is None
    assert bot._validate_decision_ref("") is None


@pytest.mark.asyncio
async def test_read_only_commands_do_not_mutate_runtime_state(tmp_path, monkeypatch):
    risk_engine = RiskEngine(_limits())
    bot = _bot(tmp_path, risk_engine=risk_engine)

    async def fake_send(_chat_id: int, _text: str) -> bool:
        return True

    async def fake_payload() -> dict[str, Any]:
        return {
            "execution_enabled": False,
            "write_back_allowed": False,
            "readiness_status": "operational",
            "cycle_count_today": 0,
            "position_count": 0,
        }

    monkeypatch.setattr(bot, "_send", fake_send)
    monkeypatch.setattr(bot, "_get_daily_operator_summary", fake_payload)

    await bot.process_update({"message": {"chat": {"id": 12345}, "text": "/status"}})

    assert bot._system_status == "operational"
    assert not risk_engine._paused
    assert not risk_engine._kill_switch_active


@pytest.mark.asyncio
async def test_help_lists_hardened_commands(tmp_path, monkeypatch):
    bot = _bot(tmp_path)
    sent_messages: list[str] = []

    async def fake_send(_chat_id: int, text: str) -> bool:
        sent_messages.append(text)
        return True

    monkeypatch.setattr(bot, "_send", fake_send)

    await bot.process_update({"message": {"chat": {"id": 12345}, "text": "/help"}})

    assert len(sent_messages) == 1
    help_text = sent_messages[0]
    assert "/signals" in help_text
    assert "/signalstatus" in help_text
    assert "/positions" in help_text
    assert "/exposure" in help_text
    assert "/approve" in help_text
    assert "/journal" not in help_text
    assert "/resolution" not in help_text
    assert "/decision_pack" not in help_text
    assert "/incident" not in help_text


@pytest.mark.asyncio
async def test_webhook_valid_secret_method_content_type_size_dispatches_once(tmp_path, monkeypatch):
    bot = _bot(
        tmp_path,
        webhook_secret_token="secret-token",
        webhook_rejection_audit_log=str(tmp_path / "webhook_rejections.jsonl"),
    )
    calls: list[tuple[int, str, str]] = []

    async def fake_dispatch(chat_id: int, command: str, *, args: str = "") -> None:
        calls.append((chat_id, command, args))

    monkeypatch.setattr(bot, "_dispatch", fake_dispatch)

    result = await bot.process_webhook_update(
        method="POST",
        content_type="application/json; charset=utf-8",
        content_length=128,
        header_secret_token="secret-token",
        update={"update_id": 101, "message": {"chat": {"id": 12345}, "text": "/status"}},
    )

    assert result.accepted is True
    assert result.processed is True
    assert result.rejection_reason is None
    assert result.update_id == 101
    assert result.update_type == "message"
    assert calls == [(12345, "status", "")]


@pytest.mark.asyncio
async def test_webhook_invalid_secret_is_rejected_and_has_no_command_side_effects(
    tmp_path, monkeypatch
):
    bot = _bot(
        tmp_path,
        webhook_secret_token="secret-token",
        webhook_rejection_audit_log=str(tmp_path / "webhook_rejections.jsonl"),
    )

    async def should_not_dispatch(*args: object, **kwargs: object) -> None:
        raise AssertionError("dispatch must not run for rejected webhook")

    monkeypatch.setattr(bot, "_dispatch", should_not_dispatch)

    result = await bot.process_webhook_update(
        method="POST",
        content_type="application/json",
        content_length=64,
        header_secret_token="wrong-token",
        update={"update_id": 11, "message": {"chat": {"id": 12345}, "text": "/status"}},
    )

    assert result.accepted is False
    assert result.processed is False
    assert result.rejection_reason == "invalid_secret_token"
    assert not (tmp_path / "cmd_audit.jsonl").exists()

    rejection_log = tmp_path / "webhook_rejections.jsonl"
    lines = [json.loads(line) for line in rejection_log.read_text(encoding="utf-8").splitlines()]
    assert lines[-1]["reason"] == "invalid_secret_token"
    assert lines[-1]["execution_enabled"] is False
    assert lines[-1]["write_back_allowed"] is False


@pytest.mark.asyncio
async def test_webhook_missing_secret_header_is_rejected_fail_closed(tmp_path, monkeypatch):
    bot = _bot(
        tmp_path,
        webhook_secret_token="secret-token",
        webhook_rejection_audit_log=str(tmp_path / "webhook_rejections.jsonl"),
    )

    async def should_not_dispatch(*args: object, **kwargs: object) -> None:
        raise AssertionError("dispatch must not run for rejected webhook")

    monkeypatch.setattr(bot, "_dispatch", should_not_dispatch)

    result = await bot.process_webhook_update(
        method="POST",
        content_type="application/json",
        content_length=64,
        header_secret_token=None,
        update={"update_id": 12, "message": {"chat": {"id": 12345}, "text": "/status"}},
    )

    assert result.accepted is False
    assert result.rejection_reason == "missing_secret_token_header"


@pytest.mark.asyncio
async def test_webhook_disallowed_update_type_is_rejected(tmp_path, monkeypatch):
    bot = _bot(
        tmp_path,
        webhook_secret_token="secret-token",
        webhook_rejection_audit_log=str(tmp_path / "webhook_rejections.jsonl"),
    )

    async def should_not_dispatch(*args: object, **kwargs: object) -> None:
        raise AssertionError("dispatch must not run for rejected webhook")

    monkeypatch.setattr(bot, "_dispatch", should_not_dispatch)

    result = await bot.process_webhook_update(
        method="POST",
        content_type="application/json",
        content_length=64,
        header_secret_token="secret-token",
        update={
            "update_id": 13,
            "channel_post": {"chat": {"id": 12345}, "text": "/status"},
        },
    )

    assert result.accepted is False
    assert result.rejection_reason == "disallowed_update_type"


@pytest.mark.asyncio
async def test_webhook_callback_query_is_accepted_when_allowed(tmp_path, monkeypatch):
    bot = _bot(
        tmp_path,
        webhook_secret_token="secret-token",
        webhook_rejection_audit_log=str(tmp_path / "webhook_rejections.jsonl"),
    )
    callback_calls: list[dict[str, Any]] = []

    async def fake_handle_callback_query(callback_query: dict[str, Any]) -> None:
        callback_calls.append(callback_query)

    monkeypatch.setattr(bot, "_handle_callback_query", fake_handle_callback_query)

    result = await bot.process_webhook_update(
        method="POST",
        content_type="application/json",
        content_length=64,
        header_secret_token="secret-token",
        update={
            "update_id": 14,
            "callback_query": {
                "id": "cbq-1",
                "from": {"id": 12345},
                "data": "cmd:status",
                "message": {"message_id": 77},
            },
        },
    )

    assert result.accepted is True
    assert result.processed is True
    assert result.update_type == "callback_query"
    assert callback_calls[0]["id"] == "cbq-1"


@pytest.mark.asyncio
async def test_webhook_duplicate_update_id_is_rejected_as_replay(tmp_path, monkeypatch):
    bot = _bot(
        tmp_path,
        webhook_secret_token="secret-token",
        webhook_rejection_audit_log=str(tmp_path / "webhook_rejections.jsonl"),
    )
    calls: list[tuple[int, str, str]] = []

    async def fake_dispatch(chat_id: int, command: str, *, args: str = "") -> None:
        calls.append((chat_id, command, args))

    monkeypatch.setattr(bot, "_dispatch", fake_dispatch)

    first = await bot.process_webhook_update(
        method="POST",
        content_type="application/json",
        content_length=64,
        header_secret_token="secret-token",
        update={"update_id": 14, "message": {"chat": {"id": 12345}, "text": "/help"}},
    )
    second = await bot.process_webhook_update(
        method="POST",
        content_type="application/json",
        content_length=64,
        header_secret_token="secret-token",
        update={"update_id": 14, "message": {"chat": {"id": 12345}, "text": "/help"}},
    )

    assert first.accepted is True
    assert second.accepted is False
    assert second.rejection_reason == "duplicate_update_id"
    assert calls == [(12345, "help", "")]

    rejection_log = tmp_path / "webhook_rejections.jsonl"
    lines = [json.loads(line) for line in rejection_log.read_text(encoding="utf-8").splitlines()]
    assert lines[-1]["reason"] == "duplicate_update_id"
    assert lines[-1]["update_id"] == 14


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "content_type", "content_length", "expected_reason"),
    [
        ("GET", "application/json", 32, "invalid_http_method"),
        ("POST", "text/plain", 32, "invalid_content_type"),
        ("POST", "application/json", None, "missing_content_length"),
        ("POST", "application/json", 0, "invalid_content_length"),
        ("POST", "application/json", 128_000, "payload_too_large"),
    ],
)
async def test_webhook_transport_checks_fail_closed(
    tmp_path,
    monkeypatch,
    method: str,
    content_type: str,
    content_length: int | None,
    expected_reason: str,
):
    bot = _bot(
        tmp_path,
        webhook_secret_token="secret-token",
        webhook_rejection_audit_log=str(tmp_path / "webhook_rejections.jsonl"),
    )

    async def should_not_dispatch(*args: object, **kwargs: object) -> None:
        raise AssertionError("dispatch must not run for rejected webhook")

    monkeypatch.setattr(bot, "_dispatch", should_not_dispatch)

    result = await bot.process_webhook_update(
        method=method,
        content_type=content_type,
        content_length=content_length,
        header_secret_token="secret-token",
        update={"update_id": 99, "message": {"chat": {"id": 12345}, "text": "/status"}},
    )

    assert result.accepted is False
    assert result.processed is False
    assert result.rejection_reason == expected_reason


@pytest.mark.asyncio
async def test_webhook_rejects_when_secret_not_configured_fail_closed(tmp_path, monkeypatch):
    bot = _bot(
        tmp_path,
        webhook_rejection_audit_log=str(tmp_path / "webhook_rejections.jsonl"),
    )

    async def should_not_dispatch(*args: object, **kwargs: object) -> None:
        raise AssertionError("dispatch must not run for rejected webhook")

    monkeypatch.setattr(bot, "_dispatch", should_not_dispatch)

    result = await bot.process_webhook_update(
        method="POST",
        content_type="application/json",
        content_length=64,
        header_secret_token="secret-token",
        update={"update_id": 55, "message": {"chat": {"id": 12345}, "text": "/status"}},
    )

    assert result.accepted is False
    assert result.processed is False
    assert result.rejection_reason == "webhook_secret_not_configured"


def test_webhook_status_summary_is_read_only(tmp_path) -> None:
    bot = _bot(
        tmp_path,
        webhook_secret_token="secret-token",
        webhook_rejection_audit_log=str(tmp_path / "webhook_rejections.jsonl"),
    )

    status = bot.get_webhook_status_summary()

    assert status["report_type"] == "telegram_webhook_status_summary"
    assert status["webhook_configured"] is True
    assert status["execution_enabled"] is False
    assert status["write_back_allowed"] is False


# ---------- Free-text processing tests ----------


class _FakeTextProcessor:
    """Stub TextIntentProcessor for unit tests."""

    def __init__(self, result: IntentResult) -> None:
        self._result = result
        self.calls: list[str] = []

    @property
    def is_configured(self) -> bool:
        return True

    async def process(self, text: str, context: str = "") -> IntentResult:
        self.calls.append(text)
        return self._result


@pytest.mark.asyncio
async def test_structured_news_is_handled_without_text_processor(tmp_path, monkeypatch):
    bot = _bot(tmp_path)
    sent: list[str] = []

    async def fake_send(_cid: int, text: str) -> bool:
        sent.append(text)
        return True

    monkeypatch.setattr(bot, "_send", fake_send)

    update = {
        "message": {
            "chat": {"id": 12345},
            "text": (
                "[NEWS]\n"
                "Source: Premium Signals\n"
                "Title: Macro pressure remains elevated\n"
                "Message: No execution signal.\n"
            ),
        }
    }
    await bot.process_update(update)

    assert len(sent) == 1
    assert "NEWS" in sent[0]
    assert "Analyse-only" in sent[0]


@pytest.mark.asyncio
async def test_structured_signal_fail_closed_on_missing_required_fields(tmp_path, monkeypatch):
    bot = _bot(tmp_path)
    sent: list[str] = []

    async def fake_send(_cid: int, text: str) -> bool:
        sent.append(text)
        return True

    async def should_not_handoff(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("invalid structured signal must not be handed off")

    monkeypatch.setattr(bot, "_send", fake_send)
    monkeypatch.setattr(bot, "_handle_signal_input", should_not_handoff)

    update = {
        "message": {
            "chat": {"id": 12345},
            "text": "[SIGNAL]\nSymbol: BTC/USDT\nDirection: LONG\n",
        }
    }
    await bot.process_update(update)

    assert len(sent) == 1
    assert "Signal blockiert" in sent[0]
    assert "missing_source" in sent[0]


@pytest.mark.asyncio
async def test_structured_signal_is_handed_off_when_valid(tmp_path, monkeypatch):
    bot = _bot(tmp_path)
    handoff_calls: list[dict[str, object]] = []

    async def fake_handoff(
        *,
        chat_id: int,
        signal: dict[str, object],
        source: str,
        response: str,
    ) -> None:
        handoff_calls.append(
            {
                "chat_id": chat_id,
                "signal": signal,
                "source": source,
                "response": response,
            }
        )

    monkeypatch.setattr(bot, "_handle_signal_input", fake_handoff)

    update = {
        "message": {
            "chat": {"id": 12345},
            "text": (
                "[SIGNAL]\n"
                "Source: Premium Signals\n"
                "Exchange Scope: Binance Futures, Bybit\n"
                "Symbol: BTC/USDT\n"
                "Direction: LONG\n"
                "Targets: 72800\n"
                "Stop Loss: 76600\n"
                "Entry Rule: BELOW 74700\n"
            ),
        }
    }
    await bot.process_update(update)

    assert len(handoff_calls) == 1
    payload = handoff_calls[0]
    assert payload["chat_id"] == 12345
    assert payload["source"] == "structured_text"
    signal_payload = payload["signal"]
    assert isinstance(signal_payload, dict)
    assert signal_payload["asset"] == "BTC"
    assert signal_payload["direction"] == "bullish"


@pytest.mark.asyncio
async def test_structured_exchange_response_is_displayed_without_text_processor(
    tmp_path, monkeypatch
):
    bot = _bot(tmp_path)
    sent: list[str] = []

    async def fake_send(_cid: int, text: str) -> bool:
        sent.append(text)
        return True

    monkeypatch.setattr(bot, "_send", fake_send)

    update = {
        "message": {
            "chat": {"id": 12345},
            "text": (
                "[EXCHANGE_RESPONSE]\n"
                "Exchange: Bybit\n"
                "Symbol: BTC/USDT\n"
                "Action: ORDER_CREATED\n"
                "Status: SUCCESS\n"
            ),
        }
    }
    await bot.process_update(update)

    assert len(sent) == 1
    assert "EXCHANGE RESPONSE" in sent[0]
    assert "Bybit" in sent[0]


@pytest.mark.asyncio
async def test_freetext_without_processor_gives_fallback(tmp_path, monkeypatch):
    """Bot without text_processor should tell user to use /help."""
    bot = _bot(tmp_path)  # no text_processor
    sent: list[str] = []

    async def fake_send(_cid: int, text: str) -> bool:
        sent.append(text)
        return True

    monkeypatch.setattr(bot, "_send", fake_send)

    update = {"message": {"chat": {"id": 12345}, "text": "Hallo KAI"}}
    await bot.process_update(update)

    assert len(sent) == 1
    assert "/help" in sent[0]


@pytest.mark.asyncio
async def test_freetext_signal_is_audited_and_confirmed(tmp_path, monkeypatch):
    """Signal intent should be audit-logged and confirmed to operator."""
    proc = _FakeTextProcessor(
        IntentResult(
            intent="signal",
            response="Notiert.",
            signal={"asset": "BTC", "direction": "bullish", "reasoning": "Breakout"},
        )
    )
    bot = _bot(tmp_path, text_processor=proc)
    sent: list[str] = []

    async def fake_send(_cid: int, text: str) -> bool:
        sent.append(text)
        return True

    monkeypatch.setattr(bot, "_send", fake_send)

    update = {"message": {"chat": {"id": 12345}, "text": "Signal: BTC bullish"}}
    await bot.process_update(update)

    assert proc.calls == ["Signal: BTC bullish"]
    assert len(sent) == 1
    assert "SIGNAL" in sent[0]
    assert "BTC" in sent[0]
    assert "Pipeline" in sent[0]

    # Audit log should have both _text and _signal_input entries
    audit = (tmp_path / "cmd_audit.jsonl").read_text(encoding="utf-8").splitlines()
    commands = [json.loads(line)["command"] for line in audit]
    assert "_text" in commands
    assert "_signal_input" in commands


@pytest.mark.asyncio
async def test_freetext_signal_handoff_pipeline_with_optional_routes(tmp_path, monkeypatch):
    proc = _FakeTextProcessor(
        IntentResult(
            intent="signal",
            response="Signal wird verarbeitet.",
            signal={"asset": "BTC", "direction": "bullish", "reasoning": "Momentum"},
        )
    )
    handoff_log = tmp_path / "signal_handoff.jsonl"
    outbox_log = tmp_path / "exchange_outbox.jsonl"
    bot = _bot(
        tmp_path,
        text_processor=proc,
        signal_handoff_log_path=str(handoff_log),
        signal_exchange_outbox_log_path=str(outbox_log),
        signal_append_decision_enabled=True,
        signal_auto_run_enabled=True,
        signal_auto_run_mode="paper",
        signal_auto_run_provider="mock",
        signal_forward_to_exchange_enabled=True,
    )
    sent: list[str] = []

    async def fake_send(_cid: int, text: str) -> bool:
        sent.append(text)
        return True

    async def fake_append_decision_from_signal(**_kwargs: Any) -> dict[str, object]:
        return {"decision_id": "dec_123456abcdef"}

    async def fake_run_signal_cycle(**_kwargs: Any) -> dict[str, object]:
        return {
            "status": "cycle_completed",
            "cycle": {"cycle_id": "cyc_001", "status": "no_signal"},
        }

    monkeypatch.setattr(bot, "_send", fake_send)
    monkeypatch.setattr(bot, "_append_decision_from_signal", fake_append_decision_from_signal)
    monkeypatch.setattr(bot, "_run_signal_cycle", fake_run_signal_cycle)

    update = {"message": {"chat": {"id": 12345}, "text": "Signal: BTC long"}}
    await bot.process_update(update)

    assert len(sent) == 1
    assert "Decision-Journal: `ok (dec_123456abcdef)`" in sent[0]
    assert "KAI-Run: `ok (no_signal)`" in sent[0]
    assert "Exchange-Forward: `queued`" in sent[0]

    handoff_rows = [
        json.loads(line)
        for line in handoff_log.read_text(encoding="utf-8").splitlines()
    ]
    assert len(handoff_rows) == 1
    assert handoff_rows[0]["event"] == "telegram_signal_handoff"
    assert handoff_rows[0]["symbol"] == "BTC/USDT"
    assert handoff_rows[0]["direction"] == "bullish"
    assert handoff_rows[0]["decision_append_status"] == "ok"
    assert handoff_rows[0]["signal_auto_run_status"] == "ok"
    assert handoff_rows[0]["exchange_forward_status"] == "queued"

    outbox_rows = [json.loads(line) for line in outbox_log.read_text(encoding="utf-8").splitlines()]
    assert len(outbox_rows) == 1
    assert outbox_rows[0]["event"] == "telegram_signal_exchange_forward_queued"
    assert outbox_rows[0]["symbol"] == "BTC/USDT"


@pytest.mark.asyncio
async def test_freetext_signal_with_invalid_asset_is_rejected(tmp_path, monkeypatch):
    proc = _FakeTextProcessor(
        IntentResult(
            intent="signal",
            response="Bitte praezisieren.",
            signal={"asset": "", "direction": "bullish", "reasoning": "test"},
        )
    )
    handoff_log = tmp_path / "signal_handoff.jsonl"
    bot = _bot(
        tmp_path,
        text_processor=proc,
        signal_handoff_log_path=str(handoff_log),
    )
    sent: list[str] = []

    async def fake_send(_cid: int, text: str) -> bool:
        sent.append(text)
        return True

    monkeypatch.setattr(bot, "_send", fake_send)

    update = {"message": {"chat": {"id": 12345}, "text": "Signal ohne Asset"}}
    await bot.process_update(update)

    assert len(sent) == 1
    assert "konnte nicht normalisiert" in sent[0]
    assert not handoff_log.exists()


@pytest.mark.asyncio
async def test_freetext_command_dispatches_to_handler(tmp_path, monkeypatch):
    """Command intent should dispatch to the matching bot command."""
    proc = _FakeTextProcessor(
        IntentResult(intent="command", response="", mapped_command="help")
    )
    bot = _bot(tmp_path, text_processor=proc)
    sent: list[str] = []

    async def fake_send(_cid: int, text: str) -> bool:
        sent.append(text)
        return True

    monkeypatch.setattr(bot, "_send", fake_send)

    update = {"message": {"chat": {"id": 12345}, "text": "Zeig mir die Hilfe"}}
    await bot.process_update(update)

    assert proc.calls == ["Zeig mir die Hilfe"]
    assert len(sent) == 1
    assert "KAI Operator Commands" in sent[0]


@pytest.mark.asyncio
async def test_freetext_query_returns_response(tmp_path, monkeypatch):
    """Query intent should return the LLM response directly."""
    proc = _FakeTextProcessor(
        IntentResult(intent="query", response="Bitcoin steht bei 95k USD.")
    )
    bot = _bot(tmp_path, text_processor=proc)
    sent: list[str] = []

    async def fake_send(_cid: int, text: str) -> bool:
        sent.append(text)
        return True

    monkeypatch.setattr(bot, "_send", fake_send)

    update = {"message": {"chat": {"id": 12345}, "text": "Wie steht Bitcoin?"}}
    await bot.process_update(update)

    assert sent == ["Bitcoin steht bei 95k USD."]


@pytest.mark.asyncio
async def test_freetext_from_unauthorized_user_is_rejected(tmp_path, monkeypatch):
    """Non-admin free text should be rejected."""
    proc = _FakeTextProcessor(IntentResult(intent="chat", response="Hi"))
    bot = _bot(tmp_path, text_processor=proc)
    sent: list[str] = []

    async def fake_send(_cid: int, text: str) -> bool:
        sent.append(text)
        return True

    monkeypatch.setattr(bot, "_send", fake_send)

    update = {"message": {"chat": {"id": 99999}, "text": "Hallo"}}
    await bot.process_update(update)

    assert proc.calls == []  # processor never called
    assert "Unauthorized" in sent[0]


# ---------- Voice message tests ----------


class _FakeVoiceTranscriber:
    """Stub VoiceTranscriber for unit tests."""

    def __init__(self, transcript: str | None) -> None:
        self._transcript = transcript
        self.calls: list[str] = []

    @property
    def is_configured(self) -> bool:
        return True

    async def transcribe(self, file_id: str) -> str | None:
        self.calls.append(file_id)
        return self._transcript


@pytest.mark.asyncio
async def test_voice_message_transcribed_and_processed(tmp_path, monkeypatch):
    """Voice → transcribe → text intent pipeline."""
    proc = _FakeTextProcessor(
        IntentResult(intent="chat", response="Verstanden!")
    )
    voice_t = _FakeVoiceTranscriber("Bitcoin ist bullish")
    bot = _bot(tmp_path, text_processor=proc, voice_transcriber=voice_t)
    sent: list[str] = []

    async def fake_send(_cid: int, text: str) -> bool:
        sent.append(text)
        return True

    monkeypatch.setattr(bot, "_send", fake_send)

    update = {
        "message": {
            "chat": {"id": 12345},
            "voice": {"file_id": "voice_abc123", "duration": 5},
        }
    }
    await bot.process_update(update)

    assert voice_t.calls == ["voice_abc123"]
    assert proc.calls == ["Bitcoin ist bullish"]
    # First message: transcript, second: intent response
    assert len(sent) == 2
    assert "Bitcoin ist bullish" in sent[0]
    assert "Verstanden!" in sent[1]

    # Audit log
    audit = (tmp_path / "cmd_audit.jsonl").read_text(encoding="utf-8").splitlines()
    commands = [json.loads(line)["command"] for line in audit]
    assert "_voice" in commands


@pytest.mark.asyncio
async def test_voice_signal_handoff_marks_source_voice(tmp_path, monkeypatch):
    proc = _FakeTextProcessor(
        IntentResult(
            intent="signal",
            response="Voice signal verarbeitet.",
            signal={"asset": "ETH", "direction": "bearish", "reasoning": "Risk-off"},
        )
    )
    voice_t = _FakeVoiceTranscriber("ETH short")
    handoff_log = tmp_path / "voice_signal_handoff.jsonl"
    bot = _bot(
        tmp_path,
        text_processor=proc,
        voice_transcriber=voice_t,
        signal_handoff_log_path=str(handoff_log),
    )
    sent: list[str] = []

    async def fake_send(_cid: int, text: str) -> bool:
        sent.append(text)
        return True

    monkeypatch.setattr(bot, "_send", fake_send)

    update = {
        "message": {
            "chat": {"id": 12345},
            "voice": {"file_id": "voice_eth", "duration": 4},
        }
    }
    await bot.process_update(update)

    assert len(sent) == 2
    assert "Transkript:" in sent[0]
    assert "SIGNAL" in sent[1]

    handoff_rows = [
        json.loads(line)
        for line in handoff_log.read_text(encoding="utf-8").splitlines()
    ]
    assert len(handoff_rows) == 1
    assert handoff_rows[0]["source"] == "voice"
    assert handoff_rows[0]["symbol"] == "ETH/USDT"


@pytest.mark.asyncio
async def test_voice_without_transcriber_gives_fallback(tmp_path, monkeypatch):
    """Voice message without transcriber → not activated message."""
    bot = _bot(tmp_path)  # no voice_transcriber, no text_processor
    sent: list[str] = []

    async def fake_send(_cid: int, text: str) -> bool:
        sent.append(text)
        return True

    monkeypatch.setattr(bot, "_send", fake_send)

    update = {
        "message": {
            "chat": {"id": 12345},
            "voice": {"file_id": "voice_xyz", "duration": 3},
        }
    }
    await bot.process_update(update)

    assert len(sent) == 1
    assert "nicht aktiviert" in sent[0]


@pytest.mark.asyncio
async def test_voice_transcription_failure_notifies_user(tmp_path, monkeypatch):
    """Failed transcription → error message to user."""
    proc = _FakeTextProcessor(IntentResult(intent="chat", response=""))
    voice_t = _FakeVoiceTranscriber(None)  # transcription fails
    bot = _bot(tmp_path, text_processor=proc, voice_transcriber=voice_t)
    sent: list[str] = []

    async def fake_send(_cid: int, text: str) -> bool:
        sent.append(text)
        return True

    monkeypatch.setattr(bot, "_send", fake_send)

    update = {
        "message": {
            "chat": {"id": 12345},
            "voice": {"file_id": "voice_fail", "duration": 2},
        }
    }
    await bot.process_update(update)

    assert voice_t.calls == ["voice_fail"]
    assert proc.calls == []  # text processor never called
    assert len(sent) == 1
    assert "nicht transkribiert" in sent[0]


@pytest.mark.asyncio
async def test_voice_from_unauthorized_user_is_rejected(tmp_path, monkeypatch):
    """Non-admin voice message → rejected."""
    voice_t = _FakeVoiceTranscriber("should not be called")
    proc = _FakeTextProcessor(IntentResult(intent="chat", response=""))
    bot = _bot(tmp_path, text_processor=proc, voice_transcriber=voice_t)
    sent: list[str] = []

    async def fake_send(_cid: int, text: str) -> bool:
        sent.append(text)
        return True

    monkeypatch.setattr(bot, "_send", fake_send)

    update = {
        "message": {
            "chat": {"id": 99999},
            "voice": {"file_id": "voice_unauth", "duration": 1},
        }
    }
    await bot.process_update(update)

    assert voice_t.calls == []
    assert "Unauthorized" in sent[0]
