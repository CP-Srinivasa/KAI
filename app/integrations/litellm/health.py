"""Erreichbarkeit des Gateways — eine Messung, keine Vermutung.

Drei Zustände, und der dritte ist der wichtigste:

    ``up``       geantwortet, Grenze eingehalten
    ``down``     nicht erreichbar oder Fehlerstatus
    ``unknown``  nicht gemessen

``unknown`` ist ausdrücklich nicht ``up``. Im ersten Anlauf galt ein Gateway
als gesund, solange niemand hingesehen hatte — und ein Health-Feld, das ohne
Messung grün ist, ist schlimmer als keines, weil man sich darauf verlässt.

Zusätzlich prüft die Sonde die **Grenze**: ADR 0017 verlangt eine kontrollierte
Localhost-Boundary. Ein erreichbares Gateway an einer Aussenadresse ist kein
gesundes Gateway, sondern ein Befund — deshalb ist ``boundary_ok`` ein eigenes
Feld und nicht in ``state`` verrechnet.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import httpx

from app.ai.audit import classify_error
from app.integrations.litellm.provider import LiteLLMConfig

GatewayState = Literal["up", "down", "unknown"]


@dataclass(frozen=True)
class GatewayHealth:
    state: GatewayState = "unknown"
    boundary_ok: bool = False
    latency_ms: float | None = None
    status_code: int | None = None
    error_class: str | None = None
    detail: str = ""

    @property
    def usable(self) -> bool:
        """Darf ueber dieses Gateway gerufen werden?

        Beides muss stimmen. Ein erreichbares Gateway ausserhalb der Grenze ist
        nicht benutzbar, auch wenn es antwortet — sonst waere die Grenze eine
        Empfehlung und keine Zusicherung.
        """
        return self.state == "up" and self.boundary_ok


def probe_gateway(*, config: LiteLLMConfig, client: httpx.Client, monotonic: Any) -> GatewayHealth:
    """Einmal anklopfen. Wirft nicht; ein Fehlschlag ist ein Befund.

    ``client`` und ``monotonic`` werden übergeben, damit die Sonde ohne Netz
    und ohne Uhr prüfbar ist.
    """
    boundary_ok = config.is_local
    started = monotonic()
    try:
        response = client.get(
            f"{config.base_url.rstrip('/')}/health/liveliness", timeout=config.timeout_s
        )
    except Exception as exc:  # noqa: BLE001 - Unerreichbarkeit ist ein Befund
        return GatewayHealth(
            state="down",
            boundary_ok=boundary_ok,
            latency_ms=(monotonic() - started) * 1000.0,
            error_class=classify_error(exc),
            detail=type(exc).__name__,
        )

    latency_ms = (monotonic() - started) * 1000.0
    if response.status_code >= 400:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            return GatewayHealth(
                state="down",
                boundary_ok=boundary_ok,
                latency_ms=latency_ms,
                status_code=response.status_code,
                error_class=classify_error(exc),
            )
    return GatewayHealth(
        state="up",
        boundary_ok=boundary_ok,
        latency_ms=latency_ms,
        status_code=response.status_code,
    )


__all__ = ["GatewayHealth", "GatewayState", "probe_gateway"]
