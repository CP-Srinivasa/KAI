"""Third-party service gate (ADR 0013, fail-closed).

Encodes the one line that runs through ADR 0013: *self-use / self-custody is open;
anything done **for third parties** (signals, portfolio management, execution,
custody) is a licensed activity* (CASP under MiCAR Titel V / BaFin under KWG/WpIG).

This module is the fail-closed guard that any future third-party-facing entrypoint
MUST call before serving an external party. It refuses unless the service is
explicitly enabled AND a documented authorization reference is present — so a
third-party path can never go live by flipping a single boolean, and never without
a recorded licence. Self-use paths do not call this guard and are unaffected.

Default-off, not wired to any consumer yet — this establishes the guard before any
third-party surface exists (build the gate before the door).
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ThirdPartyServiceSettings(BaseSettings):
    """Third-party (for-others) service configuration. Fail-closed, default-off."""

    model_config = SettingsConfigDict(
        env_prefix="THIRD_PARTY_",
        env_file=".env",
        extra="ignore",
    )

    # Master switch for ANY for-others service surface. Env THIRD_PARTY_SERVICE_ENABLED.
    service_enabled: bool = Field(default=False)
    # Documented CASP/BaFin authorization reference. REQUIRED to enable the service —
    # an empty ref keeps the gate closed even if service_enabled is True (a licence
    # must exist and be recorded before serving others). Env
    # THIRD_PARTY_BAFIN_AUTHORIZATION_REF.
    bafin_authorization_ref: str = Field(default="")


class UnlicensedThirdPartyServiceError(RuntimeError):
    """Raised when a third-party service path is reached without valid authorization."""


def require_third_party_authorization(settings: ThirdPartyServiceSettings) -> None:
    """Fail-closed gate for any third-party-facing service path (ADR 0013).

    Raises :class:`UnlicensedThirdPartyServiceError` unless the service is explicitly
    enabled AND a non-empty authorization reference is recorded. Call this at every
    for-others entrypoint; self-use paths must not call it.
    """
    if not settings.service_enabled:
        raise UnlicensedThirdPartyServiceError(
            "third-party service path is disabled (ADR 0013): serving others is a "
            "licensed activity (CASP/BaFin); THIRD_PARTY_SERVICE_ENABLED is False."
        )
    if not settings.bafin_authorization_ref.strip():
        raise UnlicensedThirdPartyServiceError(
            "third-party service enabled without a documented authorization reference "
            "(THIRD_PARTY_BAFIN_AUTHORIZATION_REF empty) — refusing (fail-closed)."
        )


__all__ = [
    "ThirdPartyServiceSettings",
    "UnlicensedThirdPartyServiceError",
    "require_third_party_authorization",
]
