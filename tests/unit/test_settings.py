from app.core.settings import (
    AlertSettings,
    AppSettings,
    DBSettings,
    ProviderSettings,
    SourceSettings,
)


def test_app_settings_defaults():
    settings = AppSettings()
    assert settings.env == "development"
    assert settings.log_level == "INFO"
    assert settings.monitor_dir == "monitor"


def test_db_settings_defaults():
    settings = DBSettings(_env_file=None)
    assert "postgresql" in settings.url
    assert settings.pool_size > 0


def test_alert_settings_defaults():
    settings = AlertSettings()
    assert settings.dry_run is True
    assert settings.telegram_enabled is False
    assert settings.email_enabled is False


def test_provider_settings_defaults():
    settings = ProviderSettings()
    assert settings.openai_model == "gpt-4o"
    assert settings.openai_timeout > 0


def test_source_settings_defaults():
    settings = SourceSettings()
    assert settings.fetch_timeout > 0
    assert settings.max_retries > 0


def test_app_settings_contains_sub_settings():
    settings = AppSettings()
    assert isinstance(settings.db, DBSettings)
    assert isinstance(settings.alerts, AlertSettings)
    assert isinstance(settings.providers, ProviderSettings)
    assert isinstance(settings.sources, SourceSettings)
