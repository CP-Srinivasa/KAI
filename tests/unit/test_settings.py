"""Tests for application settings."""

from __future__ import annotations

import pytest

from app.core.settings import AppSettings, Environment


class TestAppSettings:
    def test_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # conftest sets APP_ENV=testing globally; clear it to test the code default
        monkeypatch.delenv("APP_ENV", raising=False)
        settings = AppSettings()
        assert settings.env == Environment.DEVELOPMENT
        assert not settings.is_production
        assert settings.port == 8000

    def test_is_production(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("APP_ENV", "production")
        assert AppSettings().is_production

    def test_is_testing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("APP_ENV", "testing")
        assert AppSettings().is_testing
