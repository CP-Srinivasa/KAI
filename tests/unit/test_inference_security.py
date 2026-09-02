from __future__ import annotations

import pytest

from app.core.errors import ConfigurationError
from app.core.settings import AppSettings, InferenceSettings
from app.security.secrets import validate_secrets


def test_strict_primary_requires_gateway_key_not_direct_openai() -> None:
    settings = AppSettings(
        env="production",
        api_key="operator-api-placeholder",
        db={"url": "postgresql+asyncpg://kai:placeholder@localhost/kai"},
        providers={"openai_api_key": ""},
        inference={
            "enabled": True,
            "mode": "primary",
            "gateway_api_key": "gateway-placeholder",
        },
        _env_file=None,
    )
    validate_secrets(settings)


def test_strict_enabled_gateway_missing_key_is_refused() -> None:
    settings = AppSettings(
        env="production",
        api_key="operator-api-placeholder",
        db={"url": "postgresql+asyncpg://kai:placeholder@localhost/kai"},
        inference={"enabled": True, "mode": "primary", "gateway_api_key": ""},
        _env_file=None,
    )
    with pytest.raises(ConfigurationError, match="KAI_INFERENCE_GATEWAY_API_KEY"):
        validate_secrets(settings)


def test_gateway_secret_is_excluded_from_repr() -> None:
    marker = "credential-marker-must-not-appear"
    settings = InferenceSettings(gateway_api_key=marker, _env_file=None)
    assert marker not in repr(settings)
