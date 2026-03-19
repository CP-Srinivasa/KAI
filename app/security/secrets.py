"""Startup secrets validation.

Call validate_secrets(settings) during application startup.
Raises ConfigurationError with a clear message listing every missing value.

Rules:
- In production (APP_ENV=production): required secrets must not be empty.
- In development/testing: warnings only (so local dev works without all keys).
- Secrets are NEVER logged — only the key name is reported, never the value.
"""

from __future__ import annotations

import logging

from app.core.errors import ConfigurationError
from app.core.settings import AppSettings

logger = logging.getLogger(__name__)


def _redact(value: str) -> str:
    """Show only the first 4 chars of a secret for confirmation, rest redacted."""
    if not value:
        return "(empty)"
    if len(value) <= 4:
        return "****"
    return value[:4] + "****"


def validate_secrets(settings: AppSettings) -> None:
    """Validate required secrets are present.

    Args:
        settings: AppSettings instance.

    Raises:
        ConfigurationError: in production when required secrets are missing.
    """
    is_production = settings.env.lower() == "production"
    missing: list[str] = []
    warnings: list[str] = []

    # ── Database ──────────────────────────────────────────────────────────────
    _insecure_default = "postgresql+asyncpg://postgres:postgres@localhost:5432/ai_analyst_bot"
    db_url = settings.db.url or ""
    if not db_url or db_url == _insecure_default:
        msg = "DB_URL is using the insecure default — set a real URL with a strong password"
        if is_production:
            missing.append(msg)
        else:
            warnings.append(msg)

    # ── OpenAI ────────────────────────────────────────────────────────────────
    if not settings.providers.openai_api_key:
        msg = "OPENAI_API_KEY is empty — LLM analysis will fail"
        if is_production:
            missing.append(msg)
        else:
            warnings.append(msg)

    # ── Alert channels — validate only when enabled ───────────────────────────
    if settings.alerts.telegram_enabled:
        if not settings.alerts.telegram_token:
            missing.append("ALERT_TELEGRAM_TOKEN is empty but ALERT_TELEGRAM_ENABLED=true")
        if not settings.alerts.telegram_chat_id:
            missing.append("ALERT_TELEGRAM_CHAT_ID is empty but ALERT_TELEGRAM_ENABLED=true")

    if settings.alerts.email_enabled:
        for field, name in [
            (settings.alerts.email_host, "ALERT_EMAIL_HOST"),
            (settings.alerts.email_user, "ALERT_EMAIL_USER"),
            (settings.alerts.email_password, "ALERT_EMAIL_PASSWORD"),
            (settings.alerts.email_to, "ALERT_EMAIL_TO"),
        ]:
            if not field:
                missing.append(f"{name} is empty but ALERT_EMAIL_ENABLED=true")

    # ── Report ────────────────────────────────────────────────────────────────
    for msg in warnings:
        logger.warning("secrets_validation_warning: %s", msg)

    if missing:
        detail = "\n  - ".join(missing)
        raise ConfigurationError(
            f"Missing required secrets (APP_ENV={settings.env}):\n  - {detail}\n"
            "Set these values in your .env file or environment. "
            "Never commit secrets to version control."
        )

    logger.info(
        "secrets_validation_passed: env=%s, openai_key_prefix=%s, telegram=%s, email=%s",
        settings.env,
        _redact(settings.providers.openai_api_key),
        settings.alerts.telegram_enabled,
        settings.alerts.email_enabled,
    )
