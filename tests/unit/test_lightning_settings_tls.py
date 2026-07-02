"""A1 FIX-2 — TLS boot-guardrail on LightningSettings.

An enabled Lightning client with an empty ``tls_cert_path`` silently disables TLS
verification in the lnd REST client (``verify = tls_cert_path or False``), exposing the
macaroon + node traffic to a MITM. The model-validator refuses to construct such a
config rather than fail open. A disabled client is unaffected.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.lightning_settings import LightningSettings


def test_enabled_without_cert_is_rejected() -> None:
    with pytest.raises(ValidationError, match="APP_LN_TLS_CERT_PATH"):
        LightningSettings(enabled=True, tls_cert_path="")


def test_enabled_with_whitespace_only_cert_is_rejected() -> None:
    with pytest.raises(ValidationError):
        LightningSettings(enabled=True, tls_cert_path="   ")


def test_enabled_with_cert_is_ok() -> None:
    cfg = LightningSettings(enabled=True, tls_cert_path="/etc/lnd/tls.cert")
    assert cfg.enabled is True
    assert cfg.tls_cert_path == "/etc/lnd/tls.cert"


def test_disabled_without_cert_is_ok() -> None:
    cfg = LightningSettings(enabled=False, tls_cert_path="")
    assert cfg.enabled is False


def test_default_construction_is_ok() -> None:
    # Default is enabled=False → the guardrail never fires for the inert default.
    assert LightningSettings().enabled is False
