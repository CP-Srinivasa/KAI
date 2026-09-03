"""KAI CORE v1 — fail-closed boot validation.

Missing critical configuration must be a *startup error*, never undefined
behaviour. These tests pin the contract of ``validate_secrets`` for the two
environments that matter: production (hard fail) and development (warn only).
"""

from __future__ import annotations

import pytest

from app.core.errors import ConfigurationError
from app.core.settings import AppSettings
from app.security.secrets import validate_secrets


def _settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> AppSettings:
    for key in (
        "APP_ENV",
        "DB_URL",
        "OPENAI_API_KEY",
        "APP_API_KEY",
        "ALERT_TELEGRAM_ENABLED",
        "ALERT_EMAIL_ENABLED",
    ):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return AppSettings(_env_file=None)


def test_production_without_explicit_db_url_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(
        monkeypatch, APP_ENV="production", OPENAI_API_KEY="sk-test", APP_API_KEY="k"
    )
    assert "url" not in settings.db.model_fields_set
    with pytest.raises(ConfigurationError, match="DB_URL is not set explicitly"):
        validate_secrets(settings)


def test_production_with_explicit_sqlite_db_url_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(
        monkeypatch,
        APP_ENV="production",
        DB_URL="sqlite+aiosqlite:///./data/prod.db",
        OPENAI_API_KEY="sk-test",
        APP_API_KEY="k",
    )
    validate_secrets(settings)  # must not raise


def test_production_lists_every_missing_value_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch, APP_ENV="production")
    with pytest.raises(ConfigurationError) as excinfo:
        validate_secrets(settings)
    message = str(excinfo.value)
    assert "DB_URL" in message
    assert "OPENAI_API_KEY" in message
    assert "APP_API_KEY" in message


def test_development_defaults_only_warn(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch, APP_ENV="development")
    assert settings.db.url.startswith("sqlite+aiosqlite:///")
    validate_secrets(settings)  # warnings only — local dev must boot without keys
