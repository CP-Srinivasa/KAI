import pytest

from app.core.enums import ExecutionMode
from app.core.settings import (
    AlertSettings,
    AppSettings,
    DBSettings,
    ExecutionSettings,
    ProviderSettings,
    RiskSettings,
    SourceSettings,
    build_runtime_config_payload,
    validate_runtime_config_payload,
)


def test_app_settings_defaults(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    settings = AppSettings(_env_file=None)
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


def test_execution_settings_defaults_are_safe_and_typed():
    settings = ExecutionSettings(_env_file=None)
    assert settings.mode is ExecutionMode.PAPER
    assert settings.live_enabled is False
    assert settings.dry_run is True
    assert settings.approval_required is True


def test_execution_settings_accepts_research_mode_without_live_enablement():
    settings = ExecutionSettings(mode="research", _env_file=None)
    assert settings.mode is ExecutionMode.RESEARCH
    assert settings.live_enabled is False


def test_execution_settings_rejects_unknown_mode():
    with pytest.raises(ValueError, match="Input should be"):
        ExecutionSettings(mode="aggressive", _env_file=None)


def test_execution_settings_live_requires_explicit_enablement():
    with pytest.raises(
        ValueError,
        match="EXECUTION_MODE=live requires EXECUTION_LIVE_ENABLED=true",
    ):
        ExecutionSettings(mode="live", _env_file=None)


def test_execution_settings_live_requires_approval_and_no_dry_run():
    with pytest.raises(ValueError, match="EXECUTION_MODE=live requires EXECUTION_DRY_RUN=false"):
        ExecutionSettings(
            mode="live",
            live_enabled=True,
            dry_run=True,
            approval_required=True,
            _env_file=None,
        )
    with pytest.raises(
        ValueError,
        match="EXECUTION_MODE=live requires EXECUTION_APPROVAL_REQUIRED=true",
    ):
        ExecutionSettings(
            mode="live",
            live_enabled=True,
            dry_run=False,
            approval_required=False,
            _env_file=None,
        )


def test_execution_settings_live_enabled_requires_live_mode():
    with pytest.raises(
        ValueError,
        match="EXECUTION_LIVE_ENABLED=true requires EXECUTION_MODE=live",
    ):
        ExecutionSettings(mode="paper", live_enabled=True, _env_file=None)


def test_risk_settings_defaults_are_fail_closed():
    settings = RiskSettings(_env_file=None)
    assert settings.max_leverage == 1.0
    assert settings.require_stop_loss is True
    assert settings.allow_averaging_down is False
    assert settings.allow_martingale is False
    assert settings.kill_switch_enabled is True


def test_app_settings_contains_sub_settings():
    settings = AppSettings()
    assert isinstance(settings.db, DBSettings)
    assert isinstance(settings.alerts, AlertSettings)
    assert isinstance(settings.providers, ProviderSettings)
    assert isinstance(settings.sources, SourceSettings)
    assert isinstance(settings.execution, ExecutionSettings)
    assert isinstance(settings.risk, RiskSettings)


def test_runtime_config_payload_validates_against_schema() -> None:
    settings = AppSettings(_env_file=None)

    payload = build_runtime_config_payload(settings)

    validated = validate_runtime_config_payload(payload)
    assert validated["system_runtime"]["mode"] == "paper"
    assert validated["execution"]["live_execution_enabled"] is False
    assert validated["security"]["api_auth_required"] is True


def test_runtime_config_payload_fails_closed_on_schema_violation() -> None:
    payload = build_runtime_config_payload(AppSettings(_env_file=None))
    payload["risk"]["max_risk_per_trade_pct"] = 0.5

    with pytest.raises(ValueError, match="CONFIG_SCHEMA.json"):
        validate_runtime_config_payload(payload)


def test_app_settings_runtime_contract_rejects_invalid_risk_baseline() -> None:
    with pytest.raises(ValueError, match="CONFIG_SCHEMA.json"):
        AppSettings(
            risk=RiskSettings(max_risk_per_trade_pct=0.5, _env_file=None),
            _env_file=None,
        )
