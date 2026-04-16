from collections.abc import Mapping

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.enums import ExecutionMode
from app.core.schema_runtime import (
    validate_json_schema_payload as _validate_json_schema_payload,
)
from app.core.schema_runtime import (
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

    telegram_polling_enabled: bool = Field(default=False)
    telegram_dry_run: bool = Field(default=True)
    telegram_poll_interval_seconds: float = Field(default=1.0, gt=0.0)
    telegram_long_poll_timeout_seconds: int = Field(default=20, ge=1)
    telegram_bot_token: str = Field(default="")
    admin_chat_ids: str = Field(default="")  # Comma-separated chat IDs
    command_audit_log: str = Field(default="artifacts/operator_commands.jsonl")
    signal_handoff_log: str = Field(default="artifacts/telegram_signal_handoff.jsonl")
    signal_exchange_outbox_log: str = Field(default="artifacts/telegram_exchange_outbox.jsonl")
    signal_append_decision_enabled: bool = Field(default=False)
    signal_auto_run_enabled: bool = Field(default=False)
    signal_auto_run_mode: str = Field(default="paper")
    signal_auto_run_provider: str = Field(default="coingecko")
    signal_forward_to_exchange_enabled: bool = Field(default=False)
    signal_exchange_relay_endpoint: str = Field(default="")
    signal_exchange_relay_api_key: str = Field(default="")
    signal_exchange_relay_timeout_seconds: int = Field(default=10, ge=1)
    signal_exchange_relay_max_attempts: int = Field(default=3, ge=1)
    signal_exchange_sent_log: str = Field(default="artifacts/telegram_exchange_sent.jsonl")
    signal_exchange_dead_letter_log: str = Field(
        default="artifacts/telegram_exchange_dead_letter.jsonl"
    )

    @property
    def admin_chat_id_list(self) -> list[int]:
        if not self.admin_chat_ids:
            return []
        return [int(x.strip()) for x in self.admin_chat_ids.split(",") if x.strip()]

    @model_validator(mode="after")
    def validate_signal_handoff_mode(self) -> "OperatorSettings":
        normalized_mode = self.signal_auto_run_mode.strip().lower()
        if normalized_mode not in {"paper", "shadow"}:
            raise ValueError(
                "OPERATOR_SIGNAL_AUTO_RUN_MODE must be one of: paper, shadow."
            )
        self.signal_auto_run_mode = normalized_mode
        return self


class ExchangeSettings(BaseSettings):
    """Exchange adapter configuration.

    Set API keys via .env:
        EXCHANGE_BINANCE_API_KEY=...
        EXCHANGE_BINANCE_SECRET=...
        EXCHANGE_BYBIT_API_KEY=...
        EXCHANGE_BYBIT_SECRET=...

    Safety defaults: dry_run=True, testnet=True.
    """

    model_config = SettingsConfigDict(env_prefix="EXCHANGE_", env_file=".env", extra="ignore")

    # Global flags
    dry_run: bool = Field(default=True)
    testnet: bool = Field(default=True)
    default_exchange: str = Field(default="binance")  # binance | bybit
    whitelist: list[str] = Field(default_factory=list)  # allowed symbols

    # Binance
    binance_api_key: str = Field(default="")
    binance_secret: str = Field(default="")

    # Bybit
    bybit_api_key: str = Field(default="")
    bybit_secret: str = Field(default="")
    bybit_category: str = Field(default="spot")  # spot | linear | inverse

    # Timeouts
    timeout_seconds: float = Field(default=15.0, gt=0.0)


class TradingViewSettings(BaseSettings):
    """TradingView integration settings — TV-1 webhook ingest only.

    All defaults fail-closed: webhook is unmounted (404) unless both
    enabled=true AND a non-empty secret are configured.
    """

    model_config = SettingsConfigDict(
        env_prefix="TRADINGVIEW_", env_file=".env", extra="ignore"
    )

    webhook_enabled: bool = Field(default=False)
    webhook_secret: str = Field(default="")
    webhook_audit_log: str = Field(
        default="artifacts/tradingview_webhook_audit.jsonl"
    )
    webhook_replay_cache_size: int = Field(default=256, ge=1)
    webhook_replay_window_seconds: float = Field(default=300.0, gt=0.0)
    # TV-2.1: shared-token fallback for TradingView's native webhook which
    # cannot produce body-HMACs. Modes: hmac (default, strongest) |
    # shared_token (no body integrity) | hmac_or_token (accept either).
    webhook_auth_mode: str = Field(default="hmac")
    webhook_shared_token: str = Field(default="")
    # TV-3: when true, accepted payloads are normalized to a
    # TradingViewSignalEvent and appended to the pending-signals JSONL.
    # Default false (fail-closed). No auto-execution — events wait for
    # operator approval. Normalizer failures leave audit intact.
    webhook_signal_routing_enabled: bool = Field(default=False)
    webhook_pending_signals_log: str = Field(
        default="artifacts/tradingview_pending_signals.jsonl"
    )
    # TV-3.1: append-only operator decision log (promote / reject) and
    # promoted-candidate sink. Re-deciding an event is rejected by the CLI.
    pending_decisions_log: str = Field(
        default="artifacts/tradingview_pending_decisions.jsonl"
    )
    promoted_signals_log: str = Field(
        default="artifacts/tradingview_promoted_signals.jsonl"
    )

    @model_validator(mode="after")
    def validate_auth_mode(self) -> "TradingViewSettings":
        normalized = self.webhook_auth_mode.strip().lower()
        if normalized not in {"hmac", "shared_token", "hmac_or_token"}:
            raise ValueError(
                "TRADINGVIEW_WEBHOOK_AUTH_MODE must be one of "
                "hmac, shared_token, hmac_or_token."
            )
        if normalized in {"shared_token", "hmac_or_token"} and not self.webhook_shared_token:
            raise ValueError(
                "TRADINGVIEW_WEBHOOK_SHARED_TOKEN must be set when "
                "TRADINGVIEW_WEBHOOK_AUTH_MODE is shared_token or hmac_or_token."
            )
        self.webhook_auth_mode = normalized
        return self


class BinanceMarketDataSettings(BaseSettings):
    """TV-2 OHLCV adapter — Binance public REST (no auth).

    Gated by BINANCE_ENABLED. Used only as a supplementary market-data
    provider when explicitly enabled; CoinGecko remains the default.
    """

    model_config = SettingsConfigDict(
        env_prefix="BINANCE_", env_file=".env", extra="ignore"
    )

    enabled: bool = Field(default=False)
    base_url: str = Field(default="https://api.binance.com")
    timeout_seconds: int = Field(default=10, ge=1)
    max_retries: int = Field(default=3, ge=1)
    freshness_threshold_seconds: float = Field(default=120.0, gt=0.0)


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
    # --- Pipeline Automation ---
    # Analysis provider for automated pipeline runs (openai, anthropic, gemini, internal).
    # Set to "" to disable LLM analysis in the scheduler (rule-based only).
    pipeline_provider: str = Field(default="openai")
    # Polling interval for the RSS scheduler in minutes.
    pipeline_interval_minutes: int = Field(default=15, ge=1)

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
    tradingview: TradingViewSettings = Field(default_factory=TradingViewSettings)
    binance: BinanceMarketDataSettings = Field(default_factory=BinanceMarketDataSettings)

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
