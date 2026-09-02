from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.settings import AppSettings, InferenceSettings
from app.intelligence.settings import LlmSettings


def test_inference_defaults_are_disabled_and_off() -> None:
    settings = AppSettings(_env_file=None)
    assert settings.inference.enabled is False
    assert settings.inference.mode == "off"
    assert settings.inference.effective_mode == "off"
    assert settings.inference.gateway_url == "http://127.0.0.1:4000/v1"


def test_disabled_flag_forces_effective_off() -> None:
    settings = InferenceSettings(enabled=False, mode="primary", _env_file=None)
    assert settings.effective_mode == "off"


def test_shadow_and_primary_require_explicit_enable() -> None:
    shadow = InferenceSettings(enabled=True, mode="shadow", _env_file=None)
    primary = InferenceSettings(enabled=True, mode="primary", _env_file=None)
    assert shadow.effective_mode == "shadow"
    assert primary.effective_mode == "primary"


def test_non_loopback_gateway_is_rejected_by_default() -> None:
    with pytest.raises(ValidationError, match="must use loopback"):
        InferenceSettings(gateway_url="https://gateway.example.invalid/v1", _env_file=None)


def test_non_loopback_gateway_requires_explicit_override() -> None:
    settings = InferenceSettings(
        gateway_url="https://gateway.example.invalid/v1",
        allow_non_loopback_gateway=True,
        _env_file=None,
    )
    assert settings.allow_non_loopback_gateway is True


def test_budget_soft_limit_cannot_exceed_hard_limit() -> None:
    with pytest.raises(ValidationError, match="soft limit"):
        InferenceSettings(
            daily_soft_limit_usd=2.0,
            daily_hard_limit_usd=1.0,
            _env_file=None,
        )


def test_adr_0015_namespace_remains_independent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAI_INFERENCE_ENABLED", "true")
    monkeypatch.setenv("KAI_INFERENCE_MODE", "shadow")
    monkeypatch.setenv("KAI_LLM_ENABLED", "false")
    settings = AppSettings(_env_file=None)
    assert settings.inference.effective_mode == "shadow"
    intelligence = LlmSettings(_env_file=None)
    assert intelligence.enabled is False
    assert intelligence.mode == "disabled"
