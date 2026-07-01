"""Third-party service gate (ADR 0013, fail-closed).

Serving third parties (signals/portfolio/execution/custody) is a licensed activity
(CASP/BaFin). This guard MUST be called at every third-party entrypoint and refuses
unless the service is explicitly enabled AND a documented authorization reference is
present. Self-use is unaffected — this gate only guards *for-others* paths.
"""

from __future__ import annotations

import pytest

from app.governance.third_party_gate import (
    ThirdPartyServiceSettings,
    UnlicensedThirdPartyServiceError,
    require_third_party_authorization,
)


def test_defaults_are_inert() -> None:
    s = ThirdPartyServiceSettings(_env_file=None)
    assert s.service_enabled is False
    assert s.bafin_authorization_ref == ""


def test_guard_refuses_when_disabled() -> None:
    s = ThirdPartyServiceSettings(_env_file=None)
    with pytest.raises(UnlicensedThirdPartyServiceError):
        require_third_party_authorization(s)


def test_guard_refuses_when_enabled_without_authorization_ref() -> None:
    s = ThirdPartyServiceSettings(
        _env_file=None, service_enabled=True, bafin_authorization_ref="   "
    )
    with pytest.raises(UnlicensedThirdPartyServiceError):
        require_third_party_authorization(s)


def test_guard_passes_only_when_enabled_and_authorized() -> None:
    s = ThirdPartyServiceSettings(
        _env_file=None, service_enabled=True, bafin_authorization_ref="BaFin-CASP-2027-0001"
    )
    # must not raise
    require_third_party_authorization(s)
