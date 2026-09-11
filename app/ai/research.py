"""Role-restricted research through the existing AI control plane.

The provider is configuration, not code: this module never names a model.
It asks the ``research`` route, and which model answers is decided by
``KAI_LITELLM_RESEARCH_MODEL`` on the transport side.

The returned text is advisory evidence, never an ``AnalysisResult`` and never
an alert, score, signal, or execution instruction. Markdown is preserved
verbatim; this path deliberately has no structured-output repair.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Final

import httpx

from app.ai.config import InferenceSettings
from app.ai.runtime import LiteLLMRequest, invoke

#: Ausgabedeckel der Research-Route.
#:
#: 4096 war zu klein, und zwar nicht knapp. Am 2026-09-11 auf dem Pi gemessen,
#: echter Brief gegen das konfigurierte Research-Modell:
#:
#:     input_tokens      2177
#:     output_tokens     4096   (Deckel erreicht)
#:     reasoning_tokens  3729   <-- davon
#:     finish_reason     length · truncated True
#:
#: Ein Reasoning-Modell bezahlt sein Nachdenken aus DEMSELBEN Ausgabebudget wie
#: die Antwort. Von 4096 Token blieben rund 367 fuer den Text uebrig; die
#: Synthese brach mittendrin ab und wurde -- richtigerweise -- verworfen.
#:
#: 16384 ist ein kontrollierter Deckel, kein Maximum: der Anbieter des heute
#: konfigurierten Modells nennt selbst den doppelten Wert als Standard.
#: Theoretische Obergrenze bei voller Ausschoepfung und diesem Prompt rund
#: 0,068 USD je Brief; abgerechnet wird, was wirklich entsteht. Welches Modell
#: antwortet, steht bewusst NICHT hier — das ist Konfiguration (D-270).
#:
#: Sollte auch 16384 mit `finish_reason=length` enden, ist die naechste Frage
#: NICHT "mehr Token", sondern ob dieses Modell fuer diesen Prompt
#: unverhaeltnismaessig viel nachdenkt.
RESEARCH_MAX_TOKENS: Final[int] = 16384


class ResearchUnavailableError(RuntimeError):
    """The advisory route is disabled, blocked, or returned no usable text."""


@dataclass(frozen=True)
class ResearchAdvisoryResult:
    """Free-text evidence with measured transport metadata and zero authority."""

    content: str
    route: str
    requested_model_alias: str
    provider: str
    model: str
    http_status: int | None
    latency_ms: float
    cost_usd: float | None
    input_tokens: int | None
    output_tokens: int | None
    reasoning_tokens: int | None
    retries: int
    fallbacks: int
    finish_reason: str
    truncated: bool | None
    correlation_id: str

    execution_authority: ClassVar[bool] = False
    analysis_result_authority: ClassVar[bool] = False
    alert_authority: ClassVar[bool] = False


def _raw_research_text(body: dict[str, Any]) -> str:
    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ResearchUnavailableError("research response has no content") from exc
    if not isinstance(content, str) or not content.strip():
        raise ResearchUnavailableError("research response is empty")
    return content


async def research_advisory(
    prompt: str,
    *,
    system_prompt: str | None = None,
    settings: InferenceSettings | None = None,
    correlation_id: str | None = None,
    telemetry_path: Path | None = None,
    client_factory: Callable[..., httpx.AsyncClient] = httpx.AsyncClient,
) -> ResearchAdvisoryResult:
    """Return research text through ``app.ai`` or fail closed.

    There is intentionally no direct provider fallback. OFF, missing
    credentials, transport failure, truncation, or empty content are explicit
    unavailability rather than a second control plane or a fabricated result.
    """
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("research prompt must not be empty")

    async def no_direct_provider() -> str:
        raise ResearchUnavailableError("research route is unavailable")

    messages: list[dict[str, str]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    routed = await invoke(
        purpose="research",
        direct_call=no_direct_provider,
        direct_provider="",
        direct_model="",
        litellm=LiteLLMRequest(
            parser=_raw_research_text,
            payload={"messages": messages, "max_tokens": RESEARCH_MAX_TOKENS},
        ),
        settings=settings,
        correlation_id=correlation_id,
        telemetry_path=telemetry_path,
        client_factory=client_factory,
    )
    outcome = routed.outcome
    if routed.transport != "litellm" or outcome is None or outcome.gateway.mode != "advisory":
        raise ResearchUnavailableError("research did not use the advisory LiteLLM route")
    attempt = outcome.authoritative_attempt
    if attempt is None or not attempt.trace.identity_proven:
        raise ResearchUnavailableError("research backend identity is unproven")
    trace = attempt.trace
    reasoning = trace.detail.get("reasoning_tokens")
    transport_retries = trace.detail.get("transport_retries")
    if isinstance(transport_retries, int) and not isinstance(transport_retries, bool):
        measured_transport_retries = transport_retries
    elif isinstance(transport_retries, str):
        try:
            measured_transport_retries = int(transport_retries)
        except ValueError:
            measured_transport_retries = 0
    else:
        measured_transport_retries = 0
    status = trace.detail.get("status_code")
    return ResearchAdvisoryResult(
        content=routed.value,
        route="research",
        requested_model_alias=trace.requested_model,
        provider=trace.actual_provider,
        model=trace.actual_model,
        http_status=status if isinstance(status, int) else None,
        latency_ms=trace.latency_ms,
        cost_usd=trace.cost_usd,
        input_tokens=trace.input_tokens,
        output_tokens=trace.output_tokens,
        reasoning_tokens=reasoning if isinstance(reasoning, int) else None,
        retries=max(0, len(outcome.litellm_attempts) - 1) + measured_transport_retries,
        fallbacks=int(outcome.gateway.fell_back),
        finish_reason=str(trace.detail.get("finish_reason") or ""),
        truncated=trace.truncated,
        correlation_id=(
            outcome.gateway.litellm.correlation_id if outcome.gateway.litellm is not None else ""
        ),
    )


__all__ = [
    "RESEARCH_MAX_TOKENS",
    "ResearchAdvisoryResult",
    "ResearchUnavailableError",
    "research_advisory",
]
