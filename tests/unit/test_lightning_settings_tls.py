"""A1 FIX-2 — TLS boot-guardrail on LightningSettings.

An enabled Lightning client with an empty ``tls_cert_path`` silently disables TLS
verification in the lnd REST client (``verify = tls_cert_path or False``), exposing the
macaroon + node traffic to a MITM. The model-validator refuses to construct such a
config rather than fail open. A disabled client is unaffected.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from pydantic import ValidationError

from app.core.lightning_settings import (
    LightningBootError,
    LightningSettings,
    validate_lightning_boot,
)


def _write_self_signed(path: Path, *, not_after: datetime) -> None:
    """Write a minimal self-signed PEM cert (like lnd's tls.cert) valid until ``not_after``."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(days=3650))
        .not_valid_after(not_after)
        .sign(key, hashes.SHA256())
    )
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


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
    # _env_file=None: "Default" heisst Code-Default — auf dem Pi liegt eine echte
    # .env mit scharfen APP_LN_*-Werten, die hier nicht mitgelesen werden darf.
    assert LightningSettings(_env_file=None).enabled is False


# --- boot-time cert validator (exists / readable / PEM / not-expired) ---------------


def test_boot_validator_missing_cert_aborts(tmp_path: Path) -> None:
    cfg = LightningSettings(enabled=True, tls_cert_path=str(tmp_path / "nope.cert"))
    with pytest.raises(LightningBootError, match="does not exist"):
        validate_lightning_boot(cfg)


def test_boot_validator_expired_cert_aborts(tmp_path: Path) -> None:
    cert = tmp_path / "tls.cert"
    _write_self_signed(cert, not_after=datetime.now(UTC) - timedelta(days=1))
    cfg = LightningSettings(enabled=True, tls_cert_path=str(cert))
    with pytest.raises(LightningBootError, match="expired"):
        validate_lightning_boot(cfg)


def test_boot_validator_garbage_cert_aborts(tmp_path: Path) -> None:
    cert = tmp_path / "tls.cert"
    cert.write_bytes(b"this is not a certificate\n")
    cfg = LightningSettings(enabled=True, tls_cert_path=str(cert))
    with pytest.raises(LightningBootError, match="not a valid PEM"):
        validate_lightning_boot(cfg)


def test_boot_validator_empty_cert_file_aborts(tmp_path: Path) -> None:
    cert = tmp_path / "tls.cert"
    cert.write_bytes(b"   \n")
    cfg = LightningSettings(enabled=True, tls_cert_path=str(cert))
    with pytest.raises(LightningBootError, match="empty file"):
        validate_lightning_boot(cfg)


def test_boot_validator_valid_cert_ok(tmp_path: Path) -> None:
    cert = tmp_path / "tls.cert"
    _write_self_signed(cert, not_after=datetime.now(UTC) + timedelta(days=365))
    cfg = LightningSettings(enabled=True, tls_cert_path=str(cert))
    validate_lightning_boot(cfg)  # must not raise


def test_boot_validator_disabled_is_noop(tmp_path: Path) -> None:
    # A disabled client never touches the node → the missing cert is irrelevant.
    cfg = LightningSettings(enabled=False, tls_cert_path="")
    validate_lightning_boot(cfg)  # must not raise
