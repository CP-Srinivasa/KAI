"""Schalter der KI-Kontrollstation -- hier und NICHT in app/core/settings.py (God-File-Ratchet)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AccountKeys(BaseSettings):
    """Schluessel fuer die Guthaben-Abfrage. ``repr=False``: nie in Logs oder Fehlertexten."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    deepseek_api_key: str = Field(default="", repr=False)
    moonshot_api_key: str = Field(default="", repr=False)
    moonshot_api_base: str = Field(default="https://api.moonshot.ai/v1")
    openai_admin_key: str = Field(default="", repr=False)


class LiteLLMModels(BaseSettings):
    """Upstream-Modell je LiteLLM-Alias (dieselben Variablen wie config/litellm.yaml)."""

    model_config = SettingsConfigDict(env_prefix="KAI_LITELLM_", env_file=".env", extra="ignore")

    bulk_model: str = ""
    standard_model: str = ""
    reasoning_model: str = ""
    critical_model: str = ""
    stt_model: str = ""
    research_model: str = ""


class ControlThresholds(BaseSettings):
    """Schwellen fuer Handlungsbedarf und Telegram (Spec §6.1)."""

    model_config = SettingsConfigDict(env_prefix="KAI_AI_CONTROL_", env_file=".env", extra="ignore")

    balance_min_usd: float = 5.0
    runway_min_days: float = 7.0
    early_budget_hour_utc: int = 12
    proxy_down_minutes: int = 5
    quiet_start_hour: int = 23
    quiet_end_hour: int = 7
    quiet_tz: str = "Europe/Berlin"
    remind_after_hours: float = 24.0
    accounts_every_minutes: int = 55


@dataclass(frozen=True)
class ControlPaths:
    telemetry: Path = Path("artifacts/llm_telemetry.jsonl")
    accounts: Path = Path("artifacts/ai_accounts.json")
    runtime_dir: Path = Path("artifacts/runtime")
    protocol: Path = Path("artifacts/runtime/ai_control_protocol.json")
    alert_state: Path = Path("artifacts/runtime/ai_control_alert_state.json")
    env_file: Path = Path(".env")


def providers_configured() -> dict[str, bool]:
    """Welche direkten Schluessel gesetzt sind -- nur ja/nein, nie der Wert."""
    from app.core.settings import get_settings

    p = get_settings().providers
    return {
        "openai": bool(p.openai_api_key),
        "anthropic": bool(p.anthropic_api_key),
        "gemini": bool(p.gemini_api_key),
        "xai": bool(p.xai_api_key),
    }


__all__ = [
    "AccountKeys",
    "ControlPaths",
    "ControlThresholds",
    "LiteLLMModels",
    "providers_configured",
]
