"""Provider-neutral request/response metadata for operational inference."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class InferenceMode(StrEnum):
    OFF = "off"
    SHADOW = "shadow"
    PRIMARY = "primary"


class InferenceRoute(StrEnum):
    BULK = "bulk"
    STANDARD = "standard"
    REASONING = "reasoning"
    CRITICAL = "critical"
    STT = "stt"


class InferenceUsage(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    cached_tokens: int | None = Field(default=None, ge=0)


class AttemptTrace(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    attempt_number: int = Field(ge=1)
    requested_model_alias: str
    actual_provider: str | None = None
    actual_model: str | None = None
    latency_ms: float = Field(ge=0.0)
    success: bool
    fallback_reason: str | None = None
    error_type: str | None = None
    circuit_state: str


@dataclass(frozen=True)
class InferenceResult[ParsedT: BaseModel]:
    request_id: str
    route: InferenceRoute
    requested_model_alias: str
    actual_provider: str | None
    actual_model: str | None
    content: str
    parsed: ParsedT | None
    usage: InferenceUsage
    estimated_cost_usd: float | None
    latency_ms: float
    retry_count: int
    fallback_count: int
    schema_validation: str
    attempts: tuple[AttemptTrace, ...] = field(default_factory=tuple)
