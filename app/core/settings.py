from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    companion_model_endpoint: str | None = Field(default=None)
    companion_model_name: str = Field(default="kai-analyst-v1")
    companion_model_timeout: int = Field(default=10)

    youtube_api_key: str = Field(default="")

    @field_validator("companion_model_endpoint")
    @classmethod
    def check_local_endpoint(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v_lower = v.lower()
        allowed = ("localhost", "127.0.0.1", "host.docker.internal")
        if not any(a in v_lower for a in allowed):
            raise ValueError("Companion model endpoint MUST be localhost or internal.")
        return v


class SourceSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SOURCE_", env_file=".env", extra="ignore")

    fetch_timeout: int = Field(default=15)
    max_retries: int = Field(default=3)
    user_agent: str = Field(default="ai-analyst-bot/0.1")


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="APP_", env_file=".env", extra="ignore")

    env: str = Field(default="development")
    log_level: str = Field(default="INFO")
    monitor_dir: str = Field(default="monitor")
    # Bearer token for API auth. Empty = auth disabled (dev only). Set in production.
    api_key: str = Field(default="")

    db: DBSettings = Field(default_factory=DBSettings)
    alerts: AlertSettings = Field(default_factory=AlertSettings)
    providers: ProviderSettings = Field(default_factory=ProviderSettings)
    sources: SourceSettings = Field(default_factory=SourceSettings)


def get_settings() -> AppSettings:
    return AppSettings()
