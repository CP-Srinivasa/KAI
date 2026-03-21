"""Unit tests for TelegramOperatorBot."""
from __future__ import annotations

import json
from typing import Any

import pytest

from app.cli.main import get_invalid_research_command_refs
from app.messaging.telegram_bot import (
    TelegramOperatorBot,
    get_telegram_command_inventory,
)
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


def _bot(tmp_path, risk_engine=None) -> TelegramOperatorBot:
    return TelegramOperatorBot(
        bot_token="fake_token",
        admin_chat_ids=[12345],
        audit_log_path=str(tmp_path / "cmd_audit.jsonl"),
        risk_engine=risk_engine,
        dry_run=True,
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
    update = {"message": {"chat": {"id": 12345}, "text": "/health"}}
    await bot.process_update(update)

    audit_file = tmp_path / "cmd_audit.jsonl"
    assert audit_file.exists()
    lines = audit_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) >= 1
    record = json.loads(lines[0])
    assert record["command"] == "health"
    assert record["chat_id"] == 12345


@pytest.mark.asyncio
async def test_unknown_command(tmp_path):
    bot = _bot(tmp_path)
    update = {"message": {"chat": {"id": 12345}, "text": "/nonexistent"}}
    await bot.process_update(update)  # should not raise


@pytest.mark.asyncio
async def test_is_not_configured_without_token():
    bot = TelegramOperatorBot(
        bot_token="",
        admin_chat_ids=[],
        dry_run=True,
    )
    assert not bot.is_configured


def test_telegram_command_inventory_references_registered_cli_research_commands() -> None:
    inventory = get_telegram_command_inventory()
    refs = [
        ref
        for command_refs in inventory["canonical_research_refs"].values()
        for ref in command_refs
    ]
    assert get_invalid_research_command_refs(refs) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("command_text", "loader_name", "payload", "expected_fragments"),
    [
        (
            "/status",
            "_get_operational_readiness_summary",
            {
                "readiness_status": "warning",
                "highest_severity": "critical",
                "issue_count": 2,
                "execution_enabled": False,
                "write_back_allowed": False,
            },
            [
                "*Status (Canonical Readiness)*",
                "readiness_status=`warning`",
                "issue_count=`2`",
                "Ref: `research readiness-summary`",
            ],
        ),
        (
            "/health",
            "_get_provider_health",
            {
                "provider_count": 3,
                "healthy_count": 2,
                "degraded_count": 1,
                "unavailable_count": 0,
                "execution_enabled": False,
                "write_back_allowed": False,
            },
            [
                "*Health (Provider Surface)*",
                "provider_count=`3`",
                "Ref: `research provider-health`",
            ],
        ),
        (
            "/positions",
            "_get_paper_positions_summary",
            {
                "position_count": 2,
                "mark_to_market_status": "ok",
                "positions": [{"symbol": "BTC/USDT"}],
                "available": True,
                "execution_enabled": False,
                "write_back_allowed": False,
            },
            [
                "*Positions (Paper Portfolio Read-Only)*",
                "position_count=`2`",
                "Ref: `research paper-positions-summary`",
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
                "*Exposure (Paper Portfolio Read-Only)*",
                "mark_to_market_status=`degraded`",
                "Ref: `research paper-exposure-summary`",
            ],
        ),
        (
            "/risk",
            "_get_protective_gate_summary",
            {
                "gate_status": "blocking",
                "blocking_count": 1,
                "warning_count": 1,
                "advisory_count": 0,
                "execution_enabled": False,
                "write_back_allowed": False,
            },
            [
                "*Risk (Protective Gate)*",
                "gate_status=`blocking`",
                "Ref: `research gate-summary`",
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
                "*Signals (Read-Only Handoff)*",
                "signal_count=`1`",
                "Ref: `research signal-handoff`",
            ],
        ),
        (
            "/journal",
            "_get_review_journal_summary",
            {
                "journal_status": "open",
                "total_count": 5,
                "open_count": 2,
                "resolved_count": 3,
                "execution_enabled": False,
                "write_back_allowed": False,
            },
            [
                "*Operator Journal (Read-Only)*",
                "journal_status=`open`",
                "Ref: `research review-journal-summary`",
            ],
        ),
        (
            "/daily_summary",
            "_get_decision_pack_summary",
            {
                "overall_status": "blocking",
                "blocking_count": 1,
                "review_required_count": 2,
                "action_queue_count": 3,
                "execution_enabled": False,
                "write_back_allowed": False,
            },
            [
                "*Daily Summary (Decision Pack)*",
                "overall_status=`blocking`",
                "Ref: `research decision-pack-summary`",
            ],
        ),
        (
            "/incident latency spike",
            "_get_escalation_summary",
            {
                "escalation_status": "blocking",
                "severity": "critical",
                "blocking_count": 2,
                "operator_action_count": 2,
                "execution_enabled": False,
                "write_back_allowed": False,
            },
            [
                "*Incident (Escalation Surface)*",
                "note=`latency spike`",
                "Audit-only. No auto-remediation and no execution side effect.",
                "Ref: `research escalation-summary`",
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
    assert "execution_enabled=`False`" in text
    assert "write_back_allowed=`False`" in text
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
    monkeypatch.setattr(bot, "_get_operational_readiness_summary", failing_loader)

    await bot.process_update({"message": {"chat": {"id": 12345}, "text": "/status"}})

    assert len(sent_messages) == 1
    assert "fail-closed" in sent_messages[0].lower()
    assert "No execution side effect was performed." in sent_messages[0]


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
    monkeypatch.setattr(bot, "_get_provider_health", invalid_loader)

    await bot.process_update({"message": {"chat": {"id": 12345}, "text": "/health"}})

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
    monkeypatch.setattr(bot, "_get_operational_readiness_summary", should_not_run)

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
async def test_incident_is_append_only_audit_only(tmp_path, monkeypatch):
    bot = _bot(tmp_path)
    sent_messages: list[str] = []

    async def fake_send(_chat_id: int, text: str) -> bool:
        sent_messages.append(text)
        return True

    async def fake_loader() -> dict[str, Any]:
        return {
            "escalation_status": "review_required",
            "severity": "warning",
            "blocking_count": 0,
            "operator_action_count": 1,
            "execution_enabled": False,
            "write_back_allowed": False,
        }

    monkeypatch.setattr(bot, "_send", fake_send)
    monkeypatch.setattr(bot, "_get_escalation_summary", fake_loader)

    await bot.process_update({"message": {"chat": {"id": 12345}, "text": "/incident packet loss"}})

    assert len(sent_messages) == 1
    assert "Audit-only. No auto-remediation and no execution side effect." in sent_messages[0]
    audit_file = tmp_path / "cmd_audit.jsonl"
    lines = [json.loads(line) for line in audit_file.read_text(encoding="utf-8").splitlines()]
    assert lines[-1]["command"] == "incident"
    assert lines[-1]["args"] == "packet loss"
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
            "gate_status": "clear",
            "blocking_count": 0,
            "warning_count": 0,
            "advisory_count": 0,
        }

    monkeypatch.setattr(bot, "_send", fake_send)
    monkeypatch.setattr(bot, "_get_protective_gate_summary", fake_payload)

    await bot.process_update({"message": {"chat": {"id": 12345}, "text": "/risk"}})

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
    assert "/signals - Read-only signal handoff" in help_text
    assert "/journal - Review journal summary" in help_text
    assert "/positions - Read-only paper positions" in help_text
    assert "/exposure - Read-only paper exposure" in help_text
    assert "/approve <decision_ref> - Audit-only approval intent" in help_text
    assert "/incident <note> - Escalation summary + audit note" in help_text
