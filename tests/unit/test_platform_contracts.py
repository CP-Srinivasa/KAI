from __future__ import annotations

import json
from pathlib import Path

from app.messaging.avatar_event_interface import AvatarEvent, AvatarEventInterface
from app.messaging.persona_service import PersonaService
from app.messaging.speech_to_text_interface import (
    SpeechToTextInterface,
    SpeechToTextRequest,
)
from app.messaging.text_to_speech_interface import (
    TextToSpeechInterface,
    TextToSpeechRequest,
)

ROOT = Path(__file__).resolve().parents[2]


def test_required_platform_docs_exist() -> None:
    required = [
        "README.md",
        "ARCHITECTURE.md",
        "ASSUMPTIONS.md",
        "SECURITY.md",
        "RISK_POLICY.md",
        "DECISION_SCHEMA.json",
        "CONFIG_SCHEMA.json",
        "RUNBOOK.md",
        "TELEGRAM_INTERFACE.md",
        "CHANGELOG.md",
    ]
    for name in required:
        assert (ROOT / name).exists(), name


def test_config_schema_declares_required_groups() -> None:
    schema = json.loads((ROOT / "CONFIG_SCHEMA.json").read_text(encoding="utf-8"))
    expected_groups = {
        "system_runtime",
        "llm_agent",
        "market_data",
        "risk",
        "strategy_decision",
        "execution",
        "memory_learning",
        "security",
        "messaging_ux",
    }

    assert schema["type"] == "object"
    assert expected_groups.issubset(schema["properties"])
    assert expected_groups.issubset(set(schema["required"]))
    assert "mode" in schema["properties"]["system_runtime"]["properties"]
    assert "live_execution_enabled" in schema["properties"]["execution"]["properties"]


def test_decision_schema_requires_core_fields() -> None:
    schema = json.loads((ROOT / "DECISION_SCHEMA.json").read_text(encoding="utf-8"))
    required = {
        "decision_id",
        "timestamp_utc",
        "symbol",
        "mode",
        "thesis",
        "supporting_factors",
        "contradictory_factors",
        "confidence_score",
        "risk_assessment",
        "invalidation_condition",
        "approval_state",
        "execution_state",
    }

    assert schema["type"] == "object"
    assert required.issubset(set(schema["required"]))
    assert schema["properties"]["mode"]["enum"] == [
        "research",
        "backtest",
        "paper",
        "shadow",
        "live",
    ]


def test_telegram_interface_lists_first_class_commands() -> None:
    text = (ROOT / "TELEGRAM_INTERFACE.md").read_text(encoding="utf-8")
    for command in (
        "/status",
        "/health",
        "/positions",
        "/exposure",
        "/risk",
        "/signals",
        "/journal",
        "/approve",
        "/reject",
        "/pause",
        "/resume",
        "/kill",
        "/daily_summary",
        "/incident",
    ):
        assert command in text


def test_multichannel_interfaces_are_disabled_by_default() -> None:
    persona = PersonaService()
    tts = TextToSpeechInterface()
    stt = SpeechToTextInterface()
    avatar = AvatarEventInterface()

    assert persona.is_enabled is False
    assert persona.build_snapshot(channel="telegram").enabled is False
    assert tts.synthesize(TextToSpeechRequest(text="status")).enabled is False
    assert stt.transcribe(SpeechToTextRequest(audio_ref="clip.wav")).enabled is False
    assert avatar.publish(AvatarEvent(event_type="status")).enabled is False
