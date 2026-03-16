"""
Application Settings
====================
Centralized, environment-driven configuration using pydantic-settings.
All settings are validated at startup. Missing required values fail loudly.
"""

from __future__ import annotations

from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(str, Enum):
    DEVELOPMENT = "development"
    TESTING = "testing"
    STAGING = "staging"
    PRODUCTION = "production"


class LogLevel(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class LLMProvider(str, Enum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    LOCAL = "local"


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    name: str = Field(default="AI Analyst Trading Bot", alias="APP_NAME")
    env: Environment = Field(default=Environment.DEVELOPMENT, alias="APP_ENV")
    debug: bool = Field(default=False, alias="APP_DEBUG")
    log_level: LogLevel = Field(default=LogLevel.INFO, alias="APP_LOG_LEVEL")
    host: str = Field(default="0.0.0.0", alias="APP_HOST")
    port: int = Field(default=8000, alias="APP_PORT")
    secret_key: str = Field(default="dev-secret-key-change-in-prod", alias="SECRET_KEY")

    @property
    def is_production(self) -> bool:
        return self.env == Environment.PRODUCTION

    @property
    def is_testing(self) -> bool:
        return self.env == Environment.TESTING


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/ai_analyst_bot",
        alias="DATABASE_URL",
    )
    pool_size: int = Field(default=10, alias="DATABASE_POOL_SIZE")
    max_overflow: int = Field(default=20, alias="DATABASE_MAX_OVERFLOW")
    echo: bool = Field(default=False, alias="DATABASE_ECHO")

    @property
    def sync_url(self) -> str:
        return self.url.replace("+asyncpg", "+psycopg2")


class RedisSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")
    enabled: bool = Field(default=False, alias="REDIS_ENABLED")


class OpenAISettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    api_key: str = Field(default="", alias="OPENAI_API_KEY")
    model: str = Field(default="gpt-4o", alias="OPENAI_MODEL")
    max_tokens: int = Field(default=4096, alias="OPENAI_MAX_TOKENS")
    temperature: float = Field(default=0.1, alias="OPENAI_TEMPERATURE")
    timeout_seconds: int = Field(default=60, alias="OPENAI_TIMEOUT_SECONDS")
    max_retries: int = Field(default=3, alias="OPENAI_MAX_RETRIES")

    @field_validator("temperature")
    @classmethod
    def validate_temperature(cls, v: float) -> float:
        if not 0.0 <= v <= 2.0:
            raise ValueError("temperature must be between 0.0 and 2.0")
        return v

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key) and not self.api_key.startswith("sk-test")


class AnthropicSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    model: str = Field(default="claude-sonnet-4-6", alias="ANTHROPIC_MODEL")
    enabled: bool = Field(default=False, alias="ANTHROPIC_ENABLED")

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key) and self.enabled


class TelegramSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    chat_id: str = Field(default="", alias="TELEGRAM_CHAT_ID")
    enabled: bool = Field(default=False, alias="TELEGRAM_ENABLED")

    @property
    def is_configured(self) -> bool:
        return bool(self.bot_token) and bool(self.chat_id) and self.enabled


class EmailSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    smtp_host: str = Field(default="smtp.gmail.com", alias="EMAIL_SMTP_HOST")
    smtp_port: int = Field(default=587, alias="EMAIL_SMTP_PORT")
    smtp_user: str = Field(default="", alias="EMAIL_SMTP_USER")
    smtp_password: str = Field(default="", alias="EMAIL_SMTP_PASSWORD")
    from_address: str = Field(default="", alias="EMAIL_FROM")
    to_address: str = Field(default="", alias="EMAIL_TO")
    enabled: bool = Field(default=False, alias="EMAIL_ENABLED")
    use_tls: bool = Field(default=True, alias="EMAIL_USE_TLS")

    @property
    def is_configured(self) -> bool:
        return bool(self.smtp_user) and bool(self.smtp_password) and self.enabled


class AlertSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    min_sentiment_abs: float = Field(default=0.6, alias="ALERT_MIN_SENTIMENT_ABS")
    min_impact_score: float = Field(default=0.7, alias="ALERT_MIN_IMPACT_SCORE")
    min_novelty_score: float = Field(default=0.5, alias="ALERT_MIN_NOVELTY_SCORE")
    min_credibility_score: float = Field(default=0.5, alias="ALERT_MIN_CREDIBILITY_SCORE")
    breaking_threshold: float = Field(default=0.85, alias="ALERT_BREAKING_THRESHOLD")


class SchedulerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    rss_fetch_interval_minutes: int = Field(default=15, alias="RSS_FETCH_INTERVAL_MINUTES")
    news_fetch_interval_minutes: int = Field(default=10, alias="NEWS_FETCH_INTERVAL_MINUTES")
    youtube_fetch_interval_minutes: int = Field(default=60, alias="YOUTUBE_FETCH_INTERVAL_MINUTES")
    social_fetch_interval_minutes: int = Field(default=30, alias="SOCIAL_FETCH_INTERVAL_MINUTES")


class AnalysisSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_provider: LLMProvider = Field(default=LLMProvider.OPENAI, alias="LLM_PROVIDER")
    llm_analysis_enabled: bool = Field(default=True, alias="LLM_ANALYSIS_ENABLED")
    llm_batch_size: int = Field(default=10, alias="LLM_BATCH_SIZE")
    llm_cost_limit_usd_per_day: float = Field(default=10.0, alias="LLM_COST_LIMIT_USD_PER_DAY")


class MonitorSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    keywords_file: Path = Field(default=Path("monitor/keywords.txt"), alias="MONITOR_KEYWORDS_FILE")
    hashtags_file: Path = Field(default=Path("monitor/hashtags.txt"), alias="MONITOR_HASHTAGS_FILE")
    youtube_channels_file: Path = Field(
        default=Path("monitor/youtube_channels.txt"), alias="MONITOR_YOUTUBE_CHANNELS_FILE"
    )
    podcast_feeds_file: Path = Field(
        default=Path("monitor/podcast_feeds_resolved.txt"), alias="MONITOR_PODCAST_FEEDS_FILE"
    )
    website_sources_file: Path = Field(
        default=Path("monitor/website_sources.txt"), alias="MONITOR_WEBSITE_SOURCES_FILE"
    )
    news_domains_file: Path = Field(
        default=Path("monitor/news_domains.txt"), alias="MONITOR_NEWS_DOMAINS_FILE"
    )
    social_accounts_file: Path = Field(
        default=Path("monitor/social_accounts.txt"), alias="MONITOR_SOCIAL_ACCOUNTS_FILE"
    )
    entity_aliases_file: Path = Field(
        default=Path("monitor/entity_aliases.yml"), alias="MONITOR_ENTITY_ALIASES_FILE"
    )


class Settings(BaseSettings):
    """Root settings aggregator. Use get_settings() to access."""
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app: AppSettings = Field(default_factory=AppSettings)
    db: DatabaseSettings = Field(default_factory=DatabaseSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    openai: OpenAISettings = Field(default_factory=OpenAISettings)
    anthropic: AnthropicSettings = Field(default_factory=AnthropicSettings)
    telegram: TelegramSettings = Field(default_factory=TelegramSettings)
    email: EmailSettings = Field(default_factory=EmailSettings)
    alerts: AlertSettings = Field(default_factory=AlertSettings)
    scheduler: SchedulerSettings = Field(default_factory=SchedulerSettings)
    analysis: AnalysisSettings = Field(default_factory=AnalysisSettings)
    monitor: MonitorSettings = Field(default_factory=MonitorSettings)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached settings instance. Call once at startup."""
    return Settings()
