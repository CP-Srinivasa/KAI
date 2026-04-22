from collections.abc import Mapping

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.enums import ExecutionMode
from app.core.errors import ConfigurationError
from app.core.schema_runtime import (
    validate_json_schema_payload as _validate_json_schema_payload,
)
from app.core.schema_runtime import (
    validate_runtime_config_payload as _validate_runtime_config_payload,
)


def _strip_secret(value: object) -> object:
    # SAT-C-006: trailing newline / BOM aus copy-paste killt sonst Signaturen
    # ohne klaren Fehler ("invalid_signature" sieht wie Angriff aus, ist aber Bug).
    if isinstance(value, str):
        return value.strip().lstrip("\ufeff")
    return value


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
    telegram_token: str = Field(default="", repr=False)
    telegram_chat_id: str = Field(default="")
    email_enabled: bool = Field(default=False)
    email_host: str = Field(default="")
    email_port: int = Field(default=587)
    email_user: str = Field(default="")
    email_password: str = Field(default="", repr=False)
    email_from: str = Field(default="")
    email_to: str = Field(default="")
    dry_run: bool = Field(default=True)
    # Minimum priority score (1–10) required to trigger an alert.
    min_priority: int = Field(default=7)
    # Digest mode: accumulate alerts and send as a batch instead of individually.
    digest_enabled: bool = Field(default=False)
    digest_interval_minutes: int = Field(default=60)
    # D-125 / SAT-C-PROV-20260422-001 — HMAC secret for sealing
    # ``SignalProvenance.provenance_hash`` at alert/outcome write time. Empty
    # = hash stays None (source/version/signal_path_id still persist), which
    # is fail-open for the seal but still satisfies TV-Pivot-Bedingung 3 on
    # the three non-negotiable fields. Set in ``.env`` as
    # ``ALERT_PROVENANCE_SECRET`` to enable tamper-evident provenance.
    provenance_secret: str = Field(default="", repr=False)

    _strip_secrets = field_validator(
        "telegram_token", "email_password", "provenance_secret", mode="before"
    )(_strip_secret)


class ProviderSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", env_file=".env", extra="ignore")

    openai_api_key: str = Field(default="", repr=False)
    openai_model: str = Field(default="gpt-4o")
    openai_timeout: int = Field(default=30)

    anthropic_api_key: str = Field(default="", repr=False)
    anthropic_model: str = Field(default="claude-3-7-sonnet-20250219")
    anthropic_timeout: int = Field(default=30)

    gemini_api_key: str = Field(default="", repr=False)
    gemini_model: str = Field(default="gemini-2.5-flash")
    gemini_timeout: int = Field(default=30)


    youtube_api_key: str = Field(default="", repr=False)
    newsdata_api_key: str = Field(default="", repr=False)
    x_bearer_token: str = Field(default="", repr=False)

    _strip_secrets = field_validator(
        "openai_api_key",
        "anthropic_api_key",
        "gemini_api_key",
        "youtube_api_key",
        "newsdata_api_key",
        "x_bearer_token",
        mode="before",
    )(_strip_secret)


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

    # Position-monitor scheduler — periodic SL/TP check on open paper
    # positions.  Without this the loop only opens positions and never
    # closes them, leaving realized_pnl at 0.0.
    position_monitor_enabled: bool = Field(default=True)
    position_monitor_interval_seconds: int = Field(default=60, ge=10)

    # Operator-Signal-Bridge: turns accepted signal envelopes (from dashboard
    # paste or telegram-bot handoff) into real paper-engine fills, honoring
    # the operator's entry/SL/TP 1:1. Fail-closed: disabled by default.
    operator_signal_bridge_enabled: bool = Field(default=False)
    operator_signal_source_allowlist: str = Field(default="dashboard")  # CSV
    operator_signal_ttl_hours: int = Field(default=24, ge=1, le=168)
    operator_signal_entry_tolerance_pct: float = Field(default=0.5, ge=0.0, le=5.0)

    # Approval-Mode (Vorschlag B, B-6): per-signal manual Fill/Ignore via Telegram
    # buttons. Fail-closed: disabled by default. When enabled, parsed signals
    # from auto-ingest workers (e.g. telegram_channel) are NOT auto-routed to
    # the bridge — instead a new envelope is re-emitted with source
    # `<orig>_approved` only after the operator clicks [Fill].
    operator_signal_approval_enabled: bool = Field(default=False)
    operator_signal_approval_ttl_minutes: int = Field(default=60, ge=1, le=1440)

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
    telegram_bot_token: str = Field(default="", repr=False)
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
    signal_exchange_relay_api_key: str = Field(default="", repr=False)
    signal_exchange_relay_timeout_seconds: int = Field(default=10, ge=1)
    signal_exchange_relay_max_attempts: int = Field(default=3, ge=1)
    signal_exchange_sent_log: str = Field(default="artifacts/telegram_exchange_sent.jsonl")
    signal_exchange_dead_letter_log: str = Field(
        default="artifacts/telegram_exchange_dead_letter.jsonl"
    )
    telegram_dashboard_url: str = Field(default="")

    @property
    def admin_chat_id_list(self) -> list[int]:
        if not self.admin_chat_ids:
            return []
        return [int(x.strip()) for x in self.admin_chat_ids.split(",") if x.strip()]

    _strip_secrets = field_validator(
        "telegram_bot_token", "signal_exchange_relay_api_key", mode="before"
    )(_strip_secret)

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
    binance_api_key: str = Field(default="", repr=False)
    binance_secret: str = Field(default="", repr=False)

    # Bybit
    bybit_api_key: str = Field(default="", repr=False)
    bybit_secret: str = Field(default="", repr=False)
    bybit_category: str = Field(default="spot")  # spot | linear | inverse

    # Timeouts
    timeout_seconds: float = Field(default=15.0, gt=0.0)

    _strip_secrets = field_validator(
        "binance_api_key", "binance_secret", "bybit_api_key", "bybit_secret", mode="before"
    )(_strip_secret)


class TradingViewSettings(BaseSettings):
    """TradingView integration settings — TV-1 webhook ingest only.

    All defaults fail-closed: webhook is unmounted (404) unless both
    enabled=true AND a non-empty secret are configured.
    """

    model_config = SettingsConfigDict(
        env_prefix="TRADINGVIEW_", env_file=".env", extra="ignore"
    )

    webhook_enabled: bool = Field(default=False)
    webhook_secret: str = Field(default="", repr=False)
    webhook_audit_log: str = Field(
        default="artifacts/tradingview_webhook_audit.jsonl"
    )
    webhook_replay_cache_size: int = Field(default=256, ge=1)
    webhook_replay_window_seconds: float = Field(default=300.0, gt=0.0)
    # TV-2.1: shared-token fallback for TradingView's native webhook which
    # cannot produce body-HMACs. Modes: hmac (default, strongest) |
    # shared_token (no body integrity) | hmac_or_token (accept either).
    webhook_auth_mode: str = Field(default="hmac")
    webhook_shared_token: str = Field(default="", repr=False)
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
    # TV-4 prep: measurement-only consumer. When disabled (default),
    # the consumer is a no-op — no file is written, no state changes.
    # When enabled, each promoted row is appended once (by decision_id)
    # to the signal-audit JSONL. No trading-loop side effects.
    promoted_consumer_enabled: bool = Field(default=False)
    promoted_signal_audit_log: str = Field(
        default="artifacts/tradingview_signal_audit.jsonl"
    )
    # D-156c: periodic bridge from pending TV events into alert_audit so
    # the auto-annotator can score them for the TV-4 Quality-Bar. Default
    # off — operator opts in explicitly once the bridge is trusted.
    bridge_scheduler_enabled: bool = Field(default=False)
    bridge_scheduler_interval_seconds: int = Field(default=300, ge=30)
    bridge_scheduler_include_smoke: bool = Field(default=False)
    # SENTR-F-004: HMAC tamper-detection on tradingview_pending_signals.jsonl.
    # When set, the router signs each appended row and the bridge verifies
    # the signature before promoting the event into alert_audit.jsonl.
    # Empty = feature disabled (legacy single-trust-boundary mode).
    # Rows without _sig are counted as skipped_unsigned when the secret is
    # active — tampered (bad _sig) rows are counted as skipped_tampered.
    bridge_hmac_secret: str = Field(default="", repr=False)

    _strip_secrets = field_validator(
        "webhook_secret", "webhook_shared_token", "bridge_hmac_secret", mode="before"
    )(_strip_secret)

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


class TelegramChannelIngestSettings(BaseSettings):
    """Vorschlag B — premium-channel MTProto auto-ingest (Telethon).

    Fail-closed: disabled by default. When enabled, the worker connects
    via MTProto, resolves the target channel by title or explicit chat_id,
    subscribes to new messages, and emits parsed signals as envelope-JSONL
    records. No execution happens unless the bridge allowlist explicitly
    includes ``telegram_premium_channel`` (see B-5).
    """

    model_config = SettingsConfigDict(
        env_prefix="INGESTION_TELEGRAM_CHANNEL_", env_file=".env", extra="ignore"
    )

    enabled: bool = Field(default=False)
    # api_id/api_hash from https://my.telegram.org/apps. Required once to
    # create the session file; afterwards the session stores the auth.
    api_id: int = Field(default=0)
    api_hash: str = Field(default="", repr=False)
    # Path to the Telethon session file (persists auth state across runs).
    session_path: str = Field(default="artifacts/telegram_channel.session")
    # Resolution: prefer explicit chat_id when known, else match by title.
    # The premium channel has no @handle — title-match is the fallback.
    target_chat_id: int = Field(default=0)
    target_title: str = Field(default="")
    # Shadow-Mode: when True, the worker parses + emits envelopes but skips
    # no execution step (execution is already gated by the bridge allowlist,
    # so this is mostly for operator-side logging clarity).
    dry_run: bool = Field(default=True)
    # Source-tag written into every emitted envelope. Must match the value
    # added to EXECUTION_OPERATOR_SIGNAL_SOURCE_ALLOWLIST in B-5.
    source_tag: str = Field(default="telegram_premium_channel")
    # Diagnostic log for observed channel messages (parsed + unparsed both).
    raw_log_path: str = Field(default="artifacts/telegram_channel_raw.jsonl")


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="APP_",
        env_file=".env",
        extra="ignore",
        populate_by_name=True,
    )

    env: str = Field(default="development")
    log_level: str = Field(default="INFO")
    monitor_dir: str = Field(default="monitor")
    # Bearer token for API auth. Empty = auth disabled (dev only). Set in production.
    api_key: str = Field(default="", repr=False)
    # SENTR-F-008: zero-downtime rotation. When set, requests with Bearer
    # <api_key_next> are also accepted. Rollover flow:
    #   1. operator sets APP_API_KEY_NEXT=<new>, redeploys — both keys valid.
    #   2. clients migrate to the new key at their own pace.
    #   3. operator promotes APP_API_KEY=<new>, clears APP_API_KEY_NEXT — single
    #      key again, old key is dead.
    # Empty string = disabled (single-key mode, no behaviour change).
    api_key_next: str = Field(default="", repr=False)
    # Cloudflare Access — emails allowed to pass via Cf-Access-Authenticated-User-Email
    # header. Comma-separated string ("a@x.de,b@y.de"). Empty = CF-Access trust disabled.
    # Accepts both APP_CF_ACCESS_ALLOWED_EMAILS (prefixed) and bare CF_ACCESS_ALLOWED_EMAILS.
    cf_access_allowed_emails: str = Field(
        default="",
        validation_alias=AliasChoices(
            "APP_CF_ACCESS_ALLOWED_EMAILS",
            "CF_ACCESS_ALLOWED_EMAILS",
        ),
    )
    # --- NEO-P-001 (B): Bind-address validator ---
    # The uvicorn --host value the operator expects the server to bind to.
    # Read by scripts/server_start.sh as the primary source; the legacy
    # KAI_BIND_LAN=1 flag still works as a backwards-compatible override.
    # In production environments, a non-loopback bind (0.0.0.0, ::, *) is
    # rejected unless APP_ALLOW_NON_LOOPBACK_BIND=1 is set explicitly —
    # forces operators to make the exposure decision consciously instead
    # of inheriting it silently from a migration (e.g. Docker, reverse
    # proxy change, Pi deployment). See validate_bind_host_against_env().
    api_bind_host: str = Field(
        default="127.0.0.1",
        validation_alias=AliasChoices("APP_API_BIND_HOST", "API_BIND_HOST"),
    )
    # Opt-out for the bind-address validator. Set to True only when a
    # downstream layer (reverse proxy firewall, container network policy)
    # provides equivalent loopback-scope protection.
    allow_non_loopback_bind: bool = Field(
        default=False,
        validation_alias=AliasChoices("APP_ALLOW_NON_LOOPBACK_BIND"),
    )
    # CORS allowed origins. Comma-separated list. Override in production.
    # Example: APP_CORS_ALLOWED_ORIGINS=https://app.example.com,https://admin.example.com
    cors_allowed_origins: list[str] = Field(
        default=["http://localhost:3000", "http://localhost:8000"]
    )
    # Market data provider used by TradingLoop and operator surfaces.
    # Supported: coingecko (real, free or paid), mock (dev/test only).
    # Without API key: free tier, ~30 req/min.
    # With API key (x-cg-pro-api-key): paid tier via pro-api.coingecko.com.
    market_data_provider: str = Field(default="coingecko")
    # Optional CoinGecko Pro/Lite API key. When set, the adapter switches to
    # the pro-api.coingecko.com endpoint and sends the key via the
    # x-cg-pro-api-key header. Leave empty for free-tier.
    # Accepts both APP_COINGECKO_API_KEY (app-prefixed) and the bare
    # COINGECKO_API_KEY form already used by existing .env files.
    coingecko_api_key: str = Field(
        default="",
        repr=False,
        validation_alias=AliasChoices(
            "APP_COINGECKO_API_KEY",
            "COINGECKO_API_KEY",
        ),
    )
    # --- Pipeline Automation ---
    # Analysis provider for automated pipeline runs (openai, anthropic, gemini, internal).
    # Set to "" to disable LLM analysis in the scheduler (rule-based only).
    pipeline_provider: str = Field(default="openai")
    # Polling interval for the RSS scheduler in minutes.
    pipeline_interval_minutes: int = Field(default=15, ge=1)

    # --- Security Headers (SENTR-F-007) ---
    # Defense-in-depth for direct-path setups where the Cloudflare edge does
    # not terminate TLS or add security headers. Default enabled — disabling
    # only makes sense if a downstream reverse proxy already injects them.
    security_headers_enabled: bool = Field(default=True)
    # HSTS max-age. Default one year; set to 0 to disable the header.
    security_headers_hsts_max_age: int = Field(default=31_536_000, ge=0)
    # When True, the CSP is emitted as Content-Security-Policy-Report-Only
    # instead of enforcing. Used for a safe rollout before flipping to
    # enforce mode.
    security_headers_csp_report_only: bool = Field(default=False)
    # Additional script-src origins (space-separated) allowlisted on top of
    # the default 'self'. Useful if a future CDN or widget is added without
    # touching middleware code.
    security_headers_extra_csp_script_src: str = Field(default="")
    # When True, the CSP allows the TradingView embedded-chart widget
    # (script/frame/connect/img from *.tradingview.com plus inline scripts
    # the widget injects). Default True so the Märkte page works out of the
    # box; set False for hardened deployments that disabled the widget.
    security_headers_allow_tradingview: bool = Field(default=True)

    # --- Auth brute-force guard (SENTR-F-003) ---
    # In-memory sliding-window counter per client IP. Once the threshold is
    # reached within the window, further auth attempts return 429 with
    # Retry-After until the oldest failure ages out. Set threshold to 0 to
    # disable the guard entirely (e.g., for integration smokes that hammer
    # /dashboard/* intentionally).
    auth_rate_limit_threshold: int = Field(default=5, ge=0)
    auth_rate_limit_window_seconds: float = Field(default=300.0, gt=0.0)

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
    telegram_channel_ingest: TelegramChannelIngestSettings = Field(
        default_factory=TelegramChannelIngestSettings
    )

    _strip_secrets = field_validator(
        "api_key", "api_key_next", "coingecko_api_key", mode="before"
    )(_strip_secret)

    @model_validator(mode="after")
    def validate_bind_host_against_env(self) -> "AppSettings":
        """NEO-P-001 (B): reject non-loopback bind in production envs.

        A 0.0.0.0 / :: / * bind directly exposes the API to whatever
        network the host sits on — which is fine locally, catastrophic
        in production where the Cloudflare tunnel is the single intended
        ingress. Opt-out via APP_ALLOW_NON_LOOPBACK_BIND=1 for container
        setups that rely on a downstream firewall.
        """
        prod_envs = {"production", "prod", "live"}
        loopback = {"127.0.0.1", "localhost", "::1"}
        host = (self.api_bind_host or "").strip().lower()
        if (
            self.env.lower() in prod_envs
            and host not in loopback
            and not self.allow_non_loopback_bind
        ):
            raise ConfigurationError(
                f"APP_API_BIND_HOST='{self.api_bind_host}' is not loopback but "
                f"APP_ENV='{self.env}'. A non-loopback bind exposes the API "
                "beyond the Cloudflare tunnel. Either set APP_API_BIND_HOST=127.0.0.1 "
                "or — if a downstream firewall protects the host — set "
                "APP_ALLOW_NON_LOOPBACK_BIND=1 explicitly."
            )
        return self

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
