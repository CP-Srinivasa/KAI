"""Tests for app/persona/ stubs — verify disabled-by-default invariants."""

from __future__ import annotations

from app.persona.avatar_events import AvatarEvent, AvatarEventInterface
from app.persona.persona_service import PersonaConfig, get_persona_state
from app.persona.speech_to_text import SpeechToTextInterface, STTRequest, STTResult
from app.persona.text_to_speech import TextToSpeechInterface, TTSRequest, TTSResult


def test_persona_config_defaults_all_disabled() -> None:
    cfg = PersonaConfig()
    assert cfg.persona_enabled is False
    assert cfg.voice_enabled is False
    assert cfg.avatar_enabled is False
    assert cfg.display_name == "KAI"


def test_persona_state_defaults_inactive() -> None:
    state = get_persona_state()
    assert state.active is False
    assert state.execution_enabled is False
    assert state.write_back_allowed is False


def test_persona_state_to_json_includes_safety_fields() -> None:
    state = get_persona_state()
    payload = state.to_json_dict()
    assert payload["execution_enabled"] is False
    assert payload["write_back_allowed"] is False
    assert payload["persona_enabled"] is False


def test_tts_disabled_by_default() -> None:
    tts = TextToSpeechInterface()
    assert tts.enabled is False
    request = TTSRequest(text="Test")
    result = tts.synthesize(request)
    assert result.success is False
    assert result.execution_enabled is False
    assert "disabled" in (result.error or "").lower()


def test_stt_disabled_by_default() -> None:
    stt = SpeechToTextInterface()
    assert stt.enabled is False
    request = STTRequest(audio_path="/tmp/test.wav")
    result = stt.transcribe(request)
    assert result.success is False
    assert result.execution_enabled is False
    assert "disabled" in (result.error or "").lower()


def test_avatar_events_disabled_by_default() -> None:
    avatar = AvatarEventInterface()
    assert avatar.enabled is False
    event = AvatarEvent(
        event_type="test",
        payload={"key": "value"},
        timestamp_utc="2026-01-01T00:00:00Z",
    )
    assert event.execution_enabled is False
    assert event.write_back_allowed is False
    assert avatar.emit(event) is False


def test_persona_frozen_immutable() -> None:
    cfg = PersonaConfig()
    try:
        cfg.persona_enabled = True  # type: ignore[misc]
        raise AssertionError("Should not reach here")
    except AttributeError:
        pass  # Expected: frozen dataclass


def test_tts_result_frozen() -> None:
    result = TTSResult(success=False)
    try:
        result.success = True  # type: ignore[misc]
        raise AssertionError("Should not reach here")
    except AttributeError:
        pass


def test_stt_result_frozen() -> None:
    result = STTResult(success=False)
    try:
        result.success = True  # type: ignore[misc]
        raise AssertionError("Should not reach here")
    except AttributeError:
        pass


def test_avatar_event_frozen() -> None:
    event = AvatarEvent(event_type="x", payload={}, timestamp_utc="t")
    try:
        event.event_type = "y"  # type: ignore[misc]
        raise AssertionError("Should not reach here")
    except AttributeError:
        pass
