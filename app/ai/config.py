"""Fail-safe environment configuration owned by the AI control plane."""

from __future__ import annotations

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _strip_secret(value: object) -> object:
    return value.strip() if isinstance(value, str) else value


class InferenceSettings(BaseSettings):
    """``KAI_INFERENCE_*`` is a namespace, never a second control plane."""

    model_config = SettingsConfigDict(
        env_prefix="KAI_INFERENCE_",
        env_file=".env",
        extra="ignore",
    )

    enabled: bool = Field(default=False)
    mode_ceiling: str = Field(default="off")
    route_modes: dict[str, str] = Field(default_factory=dict)
    route_aliases: dict[str, str] = Field(
        default_factory=lambda: {
            "bulk": "kai-bulk",
            "standard": "kai-standard",
            "reasoning": "kai-reasoning",
            "critical": "kai-critical",
            "stt": "kai-stt",
        }
    )
    litellm_base_url: str = Field(default="http://127.0.0.1:4000")
    litellm_api_key: str = Field(default="", repr=False)
    timeout_seconds: float = Field(default=30.0, gt=0.0, le=300.0)
    max_attempts: int = Field(default=3, ge=1, le=3)
    backoff_base_seconds: float = Field(default=0.25, ge=0.0, le=10.0)
    backoff_max_seconds: float = Field(default=2.0, ge=0.0, le=30.0)
    jitter_max_seconds: float = Field(default=0.1, ge=0.0, le=5.0)

    _strip_api_key = field_validator("litellm_api_key", mode="before")(_strip_secret)


__all__ = ["InferenceSettings"]
