import pytest

from app.core.enums import ExecutionMode
from app.core.errors import ConfigurationError
from app.core.settings import (
    AlertSettings,
    AppSettings,
    DBSettings,
    ExecutionSettings,
    OperatorSettings,
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
    settings = AlertSettings(_env_file=None)
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


def test_operator_settings_defaults_are_fail_closed():
    settings = OperatorSettings(_env_file=None)
    assert settings.telegram_polling_enabled is False
    assert settings.telegram_dry_run is True
    assert settings.admin_chat_id_list == []
    assert settings.signal_append_decision_enabled is False
    assert settings.signal_auto_run_enabled is False
    assert settings.signal_auto_run_mode == "paper"
    assert settings.signal_forward_to_exchange_enabled is False


def test_operator_settings_rejects_invalid_signal_auto_run_mode() -> None:
    with pytest.raises(ValueError, match="OPERATOR_SIGNAL_AUTO_RUN_MODE"):
        OperatorSettings(signal_auto_run_mode="live", _env_file=None)


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


def test_execution_settings_live_requires_approval_hmac_secret():
    with pytest.raises(
        ValueError,
        match="EXECUTION_MODE=live requires EXECUTION_OPERATOR_SIGNAL_APPROVAL_HMAC_SECRET",
    ):
        ExecutionSettings(
            mode="live",
            live_enabled=True,
            dry_run=False,
            approval_required=True,
            operator_signal_approval_hmac_secret="",
            _env_file=None,
        )


def test_execution_settings_live_accepts_complete_guardrail_set():
    settings = ExecutionSettings(
        mode="live",
        live_enabled=True,
        dry_run=False,
        approval_required=True,
        operator_signal_approval_hmac_secret="test-secret-32-bytes-minimum-value",
        _env_file=None,
    )

    assert settings.mode is ExecutionMode.LIVE
    assert settings.operator_signal_approval_hmac_secret


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


# ---------------------------------------------------------------------------
# NEO-P-001 (B): bind-address validator — production rejects non-loopback
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("env", ["production", "prod", "live", "PRODUCTION"])
def test_bind_host_rejects_non_loopback_in_production(env: str) -> None:
    with pytest.raises(ConfigurationError, match="APP_API_BIND_HOST"):
        AppSettings(env=env, api_bind_host="0.0.0.0", _env_file=None)


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_bind_host_accepts_loopback_in_production(host: str) -> None:
    settings = AppSettings(env="production", api_bind_host=host, _env_file=None)
    assert settings.api_bind_host == host


def test_bind_host_non_loopback_accepted_in_dev() -> None:
    """Dev can bind 0.0.0.0 freely — exposure there is an operator choice."""
    settings = AppSettings(env="development", api_bind_host="0.0.0.0", _env_file=None)
    assert settings.api_bind_host == "0.0.0.0"


def test_bind_host_non_loopback_accepted_with_opt_out() -> None:
    """Opt-out flag lets Docker/containerised prod deployments keep 0.0.0.0."""
    settings = AppSettings(
        env="production",
        api_bind_host="0.0.0.0",
        allow_non_loopback_bind=True,
        _env_file=None,
    )
    assert settings.api_bind_host == "0.0.0.0"
    assert settings.allow_non_loopback_bind is True


def test_bind_host_default_is_loopback() -> None:
    settings = AppSettings(_env_file=None)
    assert settings.api_bind_host == "127.0.0.1"
    assert settings.allow_non_loopback_bind is False
