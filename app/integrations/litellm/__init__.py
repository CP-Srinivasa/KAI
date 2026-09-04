"""LiteLLM als Transport unterhalb der ``app.ai``-Control-Plane (ADR 0017).

Dieses Paket enthält **nur** Transport: HTTP sprechen, Antworten in
``AttemptTrace`` übersetzen, Erreichbarkeit melden. Es trifft keine Entscheidung
über Modus, Route, Budget oder Retry-Politik — die liegen in ``app.ai`` und
bleiben dort. Ein Provider, der mitentscheidet, wäre die zweite Control-Plane,
gegen die ADR 0017 geschrieben ist.

Keine neue externe Dependency: gesprochen wird OpenAI-kompatibles HTTP über
``httpx``, das KAI ohnehin einsetzt. Der LiteLLM-Prozess selbst ist ein
Betriebsmittel auf dem Pi hinter einer Localhost-Grenze, kein Python-Paket im
Baum.
"""

from app.integrations.litellm.health import GatewayHealth, probe_gateway
from app.integrations.litellm.provider import (
    LiteLLMConfig,
    call_litellm,
    trace_from_response,
)

__all__ = [
    "GatewayHealth",
    "LiteLLMConfig",
    "call_litellm",
    "probe_gateway",
    "trace_from_response",
]
