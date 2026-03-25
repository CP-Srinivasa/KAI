from collections.abc import Mapping

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.enums import ExecutionMode
from app.schemas.runtime_validator import (
    validate_json_schema_payload as _validate_json_schema_payload,
)
from app.schemas.runtime_validator import (
    validate_runtime_config_payload as _validate_runtime_config_payload,
)


def validate_json_schema_payload(
    payload: Mapping[str, object],
    *,
    schema_filename: str,
    label: str,
) -> dict[str, object]:
    """Compatibility wrapper that delegates to the canonical runtime validator."""

    return _validate_json_schema_payload(
        payload,
        schema_filename=schema_filename,
        label=label,
    )


class DBSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DB_", env_file=".env", extra="ignore")

    url: str = Field(default="postgresql+asyncpg://postgres:postgres@localhost:5432/ai_analyst_bot")
    pool_size: int = Field(default=5)
    max_overflow: int = Field(default=10)
    echo: bool = Field(default=False)


class AlertSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ALERT_", env_file=".env", extra="ignore")

    telegram_enabled: bool = Field(default=False)
    telegram_token: str = Field(default="")
    telegram_chat_id: str = Field(default="")
    email_enabled: bool = Field(default=False)
    email_host: str = Field(default="")
    email_port: int = Field(default=587)
    email_user: str = Field(default="")
    email_password: str = Field(default="")
    email_from: str = Field(default="")
    email_to: str = Field(default="")
    dry_run: bool = Field(default=True)
    # Minimum priority score (1–10) required to trigger an alert.
    min_priority: int = Field(default=7)
    # Digest mode: accumulate alerts and send as a batch instead of individually.
    digest_enabled: bool = Field(default=False)
    digest_interval_minutes: int = Field(default=60)


class ProviderSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", env_file=".env", extra="ignore")

    openai_api_key: str = Field(default="")
    openai_model: str = Field(default="gpt-4o")
    openai_timeout: int = Field(default=30)

    anthropic_api_key: str = Field(default="")
    anthropic_model: str = Field(default="claude-3-7-sonnet-20250219")
    anthropic_timeout: int = Field(default=30)

    gemini_api_key: str = Field(default="")
    gemini_model: str = Field(default="gemini-2.5-flash")
    gemini_timeout: int = Field(default=30)


    youtube_api_key: str = Field(default="")
    newsdata_api_key: str = Field(default="")


class SourceSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SOURCE_", env_file=".env", extra="ignore")

    fetch_timeout: int = Field(default=15)
    max_retries: int = Field(default=3)
    user_agent: str = Field(default="ai-analyst-bot/0.1")


class RiskSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RISK_", env_file=".env", extra="ignore")

    # Capital limits
    initial_equity: float = Field(default=10000.0)
    max_risk_per_trade_pct: float = Field(default=0.25)  # max 0.25% per trade
    max_daily_loss_pct: float = Field(default=1.0)  # max 1% daily loss
    max_total_drawdown_pct: float = Field(default=5.0)  # max 5% drawdown
    max_open_positions: int = Field(default=3)
    max_leverage: float = Field(default=1.0)

    # Safety gates (must remain True)
    require_stop_loss: bool = Field(default=True)
    allow_averaging_down: bool = Field(default=False)
    allow_martingale: bool = Field(default=False)
    kill_switch_enabled: bool = Field(default=True)

    # Signal quality gates
    min_signal_confidence: float = Field(default=0.75)
    min_signal_confluence_count: int = Field(default=2)

    # Cooldown after loss/error (minutes)
    cooldown_after_loss_minutes: int = Field(default=30)
    cooldown_after_error_minutes: int = Field(default=10)


class ExecutionSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EXECUTION_", env_file=".env", extra="ignore")

    # Safety: always paper by default
    mode: ExecutionMode = Field(default=ExecutionMode.PAPER)
    live_enabled: bool = Field(default=False)
    dry_run: bool = Field(default=True)
    approval_required: bool = Field(default=True)

    # Paper trading
    paper_initial_equity: float = Field(default=10000.0)
    paper_fee_pct: float = Field(default=0.1)  # 0.1% fee
    paper_slippage_pct: float = Field(default=0.05)  # 0.05% slippage

    # Order parameters
    order_ttl_seconds: int = Field(default=300)
    max_order_retries: int = Field(default=3)
    execution_timeout_seconds: int = Field(default=30)

    @model_validator(mode="after")
    def validate_mode_guardrails(self) -> "ExecutionSettings":
        if self.live_enabled and self.mode is not ExecutionMode.LIVE:
            raise ValueError("EXECUTION_LIVE_ENABLED=true requires EXECUTION_MODE=live.")
        if self.mode is ExecutionMode.LIVE:
            if not self.live_enabled:
                raise ValueError("EXECUTION_MODE=live requires EXECUTION_LIVE_ENABLED=true.")
            if self.dry_run:
                raise ValueError("EXECUTION_MODE=live requires EXECUTION_DRY_RUN=false.")
            if not self.approval_required:
                raise ValueError("EXECUTION_MODE=live requires EXECUTION_APPROVAL_REQUIRED=true.")
        return self


class OperatorSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OPERATOR_", env_file=".env", extra="ignore")

    telegram_bot_token: str = Field(default="")
    admin_chat_ids: str = Field(default="")  # Comma-separated chat IDs
    command_audit_log: str = Field(default="artifacts/operator_commands.jsonl")

    @property
    def admin_chat_id_list(self) -> list[int]:
        if not self.admin_chat_ids:
            return []
        return [int(x.strip()) for x in self.admin_chat_ids.split(",") if x.strip()]


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="APP_", env_file=".env", extra="ignore")

    env: str = Field(default="development")
    log_level: str = Field(default="INFO")
    monitor_dir: str = Field(default="monitor")
    # Bearer token for API auth. Empty = auth disabled (dev only). Set in production.
    api_key: str = Field(default="")
    # CORS allowed origins. Comma-separated list. Override in production.
    # Example: APP_CORS_ALLOWED_ORIGINS=https://app.example.com,https://admin.example.com
    cors_allowed_origins: list[str] = Field(
        default=["http://localhost:3000", "http://localhost:8000"]
    )
    # Market data provider used by TradingLoop and operator surfaces.
    # Supported: coingecko (real, free-tier, delayed ~1min), mock (dev/test only).
    # CoinGecko free tier: ~30 req/min, spot price only, no auth required.
    market_data_provider: str = Field(default="coingecko")
    # --- Request Governance (Sprint 44) ---
    # Maximum request body size in bytes. Requests exceeding this limit are
    # rejected with HTTP 413 before reaching route handlers. Default: 64 KiB.
    max_request_body_bytes: int = Field(default=65_536, ge=1)
    # Guarded-endpoint rate-limit: maximum requests per sliding window per subject.
    # Window duration is APP_RATE_LIMIT_WINDOW_SECONDS.
    rate_limit_per_window: int = Field(default=5, ge=1)
    # Sliding-window duration in seconds for guarded-endpoint rate-limiting.
    rate_limit_window_seconds: float = Field(default=30.0, gt=0.0)
    # Idempotency replay window in seconds.  Responses cached for this duration.
    # Default: 300 s (5 min). A value of 0 disables idempotency caching.
    idempotency_window_seconds: float = Field(default=300.0, ge=0.0)

    db: DBSettings = Field(default_factory=DBSettings)
    alerts: AlertSettings = Field(default_factory=AlertSettings)
    providers: ProviderSettings = Field(default_factory=ProviderSettings)
    sources: SourceSettings = Field(default_factory=SourceSettings)
    risk: RiskSettings = Field(default_factory=RiskSettings)
    execution: ExecutionSettings = Field(default_factory=ExecutionSettings)
    operator: OperatorSettings = Field(default_factory=OperatorSettings)

    @model_validator(mode="after")
    def validate_runtime_contract(self) -> "AppSettings":
        validate_runtime_config_payload(self.to_runtime_config_payload())
        return self

    def to_runtime_config_payload(self) -> dict[str, object]:
        return build_runtime_config_payload(self)


def build_runtime_config_payload(settings: AppSettings) -> dict[str, object]:
    """Project the current AppSettings instance into the bundled config contract."""

    primary_model = settings.providers.openai_model or "gpt-4o"
    fallback_model = (
        settings.providers.anthropic_model or settings.providers.gemini_model or primary_model
    )
    return {
        "system_runtime": {
            "app_name": "KAI",
            "environment": settings.env,
            "mode": settings.execution.mode.value,
            "timezone_internal": "UTC",
            "timezone_display": "UTC",
            "log_level": settings.log_level,
            "debug": settings.env.lower() in {"development", "dev", "local"},
            "dry_run": settings.execution.dry_run,
            "safe_mode": True,
            "maintenance_mode": False,
            "service_version": "0.1.0",
        },
        "llm_agent": {
            "primary_model": primary_model,
            "fallback_model": fallback_model,
            "max_tokens": 2048,
            "temperature": 0.0,
            "timeout_seconds": settings.providers.openai_timeout,
            "retry_count": settings.sources.max_retries,
            "max_tool_calls": 4,
            "max_plan_steps": 8,
            "reasoning_budget": 0,
            "response_schema_version": "1.0",
            "prompt_version": "v1",
            "planner_model": primary_model,
            "executor_model": primary_model,
            "validator_model": fallback_model,
            "reflection_enabled": False,
            "self_critique_enabled": False,
        },
        "market_data": {
            "enabled_data_sources": ["rss"],
            "symbols_whitelist": [],
            "exchanges_whitelist": [],
            "timeframes": ["1h", "4h", "1d"],
            "data_freshness_threshold_seconds": 3600,
            "candle_gap_tolerance": 1,
            "max_news_age_minutes": 240,
            "sentiment_source_weights": {"rule_based": 1.0},
            "macro_source_weights": {"calendar": 1.0},
            "orderbook_depth_required": 0.0,
            "stale_data_fail_policy": "fail_closed",
            "min_source_count_for_decision": 1,
        },
        "risk": {
            "initial_equity_reference": settings.risk.initial_equity,
            "max_risk_per_trade_pct": settings.risk.max_risk_per_trade_pct,
            "max_daily_loss_pct": settings.risk.max_daily_loss_pct,
            "max_total_drawdown_pct": settings.risk.max_total_drawdown_pct,
            "max_open_positions": settings.risk.max_open_positions,
            "max_sector_or_theme_exposure_pct": 25.0,
            "max_correlated_exposure_pct": 50.0,
            "max_leverage": settings.risk.max_leverage,
            "require_stop_loss": settings.risk.require_stop_loss,
            "allow_averaging_down": settings.risk.allow_averaging_down,
            "allow_martingale": settings.risk.allow_martingale,
            "allow_unbounded_loss": False,
            "slippage_limit_bps": 25,
            "fee_buffer_bps": 10,
            "min_liquidity_threshold": 1000000.0,
            "kill_switch_enabled": settings.risk.kill_switch_enabled,
            "max_position_holding_time": "24h",
            "cooldown_after_loss_minutes": settings.risk.cooldown_after_loss_minutes,
            "cooldown_after_error_minutes": settings.risk.cooldown_after_error_minutes,
        },
        "strategy_decision": {
            "min_signal_confidence": settings.risk.min_signal_confidence,
            "min_signal_confluence_count": settings.risk.min_signal_confluence_count,
            "regime_filter_enabled": True,
            "volatility_filter_enabled": True,
            "liquidity_filter_enabled": True,
            "news_risk_filter_enabled": True,
            "macro_event_filter_enabled": True,
            "invalidation_rule_required": True,
            "thesis_required": True,
            "contradiction_check_required": True,
            "scenario_analysis_required": True,
        },
        "execution": {
            "order_type_policy": "market_or_limit",
            "order_ttl_seconds": settings.execution.order_ttl_seconds,
            "max_order_retries": settings.execution.max_order_retries,
            "idempotency_key_required": True,
            "execution_timeout_seconds": settings.execution.execution_timeout_seconds,
            "partial_fill_policy": "cancel_remaining",
            "reconciliation_interval_seconds": 60,
            "exchange_heartbeat_timeout_seconds": 30,
            "broker_failover_policy": "fail_closed",
            "live_execution_enabled": settings.execution.live_enabled,
            "approval_required_for_live_actions": settings.execution.approval_required,
        },
        "memory_learning": {
            "memory_enabled": True,
            "episodic_memory_ttl_days": 30,
            "strategy_journal_enabled": True,
            "operator_feedback_enabled": True,
            "self_improvement_enabled": False,
            "self_modification_in_production": False,
            "learning_review_required": True,
            "knowledge_source_trust_ranking": {
                "operator": 1.0,
                "validated_model": 0.7,
            },
            "model_eval_threshold": 0.8,
            "drift_detection_enabled": True,
            "rollback_required_for_learning_updates": True,
            "memory_compaction_policy": "append_only",
            "memory_retention_policy": "audit_first",
        },
        "security": {
            "secret_backend": "environment",
            "allowed_hosts": ["localhost", "127.0.0.1"],
            "webhook_signature_required": True,
            "api_auth_required": True,
            "RBAC_enabled": True,
            "audit_log_immutable": True,
            "encryption_at_rest_required": True,
            "encryption_in_transit_required": True,
            "prompt_injection_filter_enabled": True,
            "sandbox_required_for_code_execution": True,
            "dependency_scan_required": True,
            "secret_scan_required": True,
        },
        "messaging_ux": {
            "telegram_enabled": settings.alerts.telegram_enabled,
            "telegram_admin_chat_ids": settings.operator.admin_chat_id_list,
            "alert_severity_threshold": "warning",
            "summary_schedule": "manual",
            "voice_interface_enabled": False,
            "avatar_interface_enabled": False,
            "operator_approval_required_for_critical_actions": True,
        },
    }


def validate_runtime_config_payload(payload: Mapping[str, object]) -> dict[str, object]:
    """Validate a runtime config payload against the bundled KAI config schema."""

    return _validate_runtime_config_payload(payload)


def get_settings() -> AppSettings:
    return AppSettings()
