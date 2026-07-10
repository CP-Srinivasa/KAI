"""Fail-closed settings for the intelligence layer (ADR 0015 §2).

Deliberately NOT nested into ``AppSettings``: the layer stays self-contained
(and ``app/core/settings.py`` is a god-file under ratchet). Env names are exactly
the ADR-mandated ``KAI_LLM_*`` flags.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class LlmSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="KAI_LLM_", env_file=".env", extra="ignore")

    enabled: bool = Field(default=False)
    mode: Literal["disabled", "shadow"] = Field(default="disabled")
    provider: Literal["none", "mock", "ollama", "claude"] = Field(default="none")
    # Layer constant. Read for auditability, but ``true`` is refused at load —
    # there is no legitimate configuration in which LLM output reaches execution.
    influences_execution: bool = Field(default=False)
    ollama_base_url: str = Field(default="http://localhost:11434")
    model: str = Field(default="")  # empty = unavailable; NEVER auto-installed
    timeout_s: float = Field(default=120.0)
    max_tokens: int = Field(default=2048)
    context_allowlist: str = Field(
        default="artifacts/daily_strategy,artifacts/agents/daily_review,docs/adr,docs/runbooks"
    )

    def allowlist_paths(self) -> tuple[str, ...]:
        return tuple(p.strip() for p in self.context_allowlist.split(",") if p.strip())


class LlmConfigRefusedError(RuntimeError):
    """Raised when a forbidden configuration is present (fail-closed boot check)."""


@lru_cache(maxsize=1)
def get_llm_settings() -> LlmSettings:
    settings = LlmSettings()
    if settings.influences_execution:
        raise LlmConfigRefusedError(
            "KAI_LLM_INFLUENCES_EXECUTION=true is refused: LLM output is untrusted "
            "analysis and must never influence execution (ADR 0015)."
        )
    return settings
