"""Fail-safe environment configuration owned by the AI control plane."""

from __future__ import annotations

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.ai.retry import DEFAULT_MAX_ATTEMPTS, MAX_ATTEMPTS_CEILING


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
    #: Logische Route -> Denkbudget in Token. LEER heisst: kein Parameter, also
    #: unveraendertes Verhalten des Modells.
    #:
    #: Am 2026-09-08 auf kai-pi5 gemessen, `gemini/gemini-2.5-flash`, derselbe
    #: Prompt:
    #:
    #:   ohne Parameter   381 Denk-Token, 15 Text-Token, 0,0009957 USD, 3632 ms
    #:   Budget 128       104 Denk-Token, 45 Text-Token, 0,0003782 USD, 2354 ms
    #:   Budget 0           0 Denk-Token, 42 Text-Token, 0,0001107 USD,  772 ms
    #:
    #: Der Faktor zwischen "denken" und "nicht denken" ist 9, bei fuenffacher
    #: Geschwindigkeit -- und die Antwort wurde dabei laenger, nicht kuerzer.
    #: Ob sie BESSER war, sagt diese Messung nicht; das ist der Grund, warum es
    #: ein Regler ist und keine Vorgabe.
    #:
    #: Bewusst ein Token-Budget und keine Stufe: `reasoning_effort="low"` wurde
    #: in derselben Messung durchgereicht und blieb wirkungslos (380 statt 381
    #: Denk-Token). Wer es setzte, glaubte zu sparen und sparte nichts.
    route_reasoning_budget: dict[str, int] = Field(default_factory=dict)
    litellm_base_url: str = Field(default="http://127.0.0.1:4000")
    litellm_api_key: str = Field(default="", repr=False)
    timeout_seconds: float = Field(default=30.0, gt=0.0, le=300.0)
    # Die Obergrenze wird nicht zweitgeschrieben: sie gehoert der Retry-Politik.
    max_attempts: int = Field(default=DEFAULT_MAX_ATTEMPTS, ge=1, le=MAX_ATTEMPTS_CEILING)
    backoff_base_seconds: float = Field(default=0.25, ge=0.0, le=10.0)
    backoff_max_seconds: float = Field(default=2.0, ge=0.0, le=30.0)
    jitter_max_seconds: float = Field(default=0.1, ge=0.0, le=5.0)

    _strip_api_key = field_validator("litellm_api_key", mode="before")(_strip_secret)


__all__ = ["InferenceSettings"]
