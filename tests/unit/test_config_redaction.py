"""KAI CORE v1 — the effective configuration is provable, secrets are not."""

from __future__ import annotations

import json

import pytest

from app.core.config_redaction import (
    REDACTED_EMPTY,
    assert_no_secret_leak,
    fingerprint,
    redacted_config_snapshot,
)
from app.core.settings import AppSettings

_SECRETS = {
    "OPENAI_API_KEY": "sk-live-abcdef0123456789",
    "ANTHROPIC_API_KEY": "sk-ant-zzz999",
    "APP_API_KEY": "operator-bearer-key-42",
    "ALERT_TELEGRAM_TOKEN": "123456:ABCDEF-telegram",
    "DB_URL": "postgresql+asyncpg://kai:hunter2@db.internal:5432/kai",
}


def _settings(monkeypatch: pytest.MonkeyPatch) -> AppSettings:
    for key, value in _SECRETS.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("EXECUTION_ENTRY_MODE", "paper")
    return AppSettings(_env_file=None)


def test_snapshot_never_contains_a_secret_value(monkeypatch: pytest.MonkeyPatch) -> None:
    snap = redacted_config_snapshot(_settings(monkeypatch))
    assert_no_secret_leak(snap, list(_SECRETS.values()))
    blob = json.dumps(snap)
    assert "hunter2" not in blob
    assert "sk-live" not in blob


def test_secrets_are_fingerprinted_not_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    snap = redacted_config_snapshot(_settings(monkeypatch))
    providers = snap["sections"]["providers"]
    assert providers["openai_api_key"] == fingerprint(_SECRETS["OPENAI_API_KEY"])
    assert providers["openai_api_key"].startswith("(set:")
    assert providers["gemini_api_key"] == REDACTED_EMPTY
    assert snap["sections"]["app"]["api_key"] == fingerprint(_SECRETS["APP_API_KEY"])


def test_db_url_keeps_host_but_drops_userinfo(monkeypatch: pytest.MonkeyPatch) -> None:
    snap = redacted_config_snapshot(_settings(monkeypatch))
    assert snap["sections"]["db"]["url"] == "postgresql+asyncpg://***@db.internal:5432/kai"


def test_explicit_lists_env_provided_fields_only(monkeypatch: pytest.MonkeyPatch) -> None:
    snap = redacted_config_snapshot(_settings(monkeypatch))
    assert "openai_api_key" in snap["explicit"]["providers"]
    assert "gemini_api_key" not in snap["explicit"].get("providers", [])
    assert "url" in snap["explicit"]["db"]
    assert "entry_mode" in snap["explicit"]["execution"]
    assert snap["sections"]["app"]["env"] == "testing"


def test_non_secret_operational_values_are_visible(monkeypatch: pytest.MonkeyPatch) -> None:
    snap = redacted_config_snapshot(_settings(monkeypatch))
    assert snap["sections"]["execution"]["entry_mode"] == "paper"
    assert snap["sections"]["providers"]["openai_model"]
