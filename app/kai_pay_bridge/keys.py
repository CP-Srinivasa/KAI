"""Signierschluessel der Vorschlagsquelle: anlegen und laden (D-297).

- Angelegt wird ohne Fenster, in dem die Datei fremdlesbar ist:
  ``os.open(O_CREAT|O_EXCL, 0o600)`` wie in ``scripts/hotp_provision.py``; das Verzeichnis
  bekommt 0700. Eine vorhandene Datei wird nie ueberschrieben.
- Die Datei liegt unter ``~/kai-secrets`` und wandert damit verschluesselt mit dem Vault-Lauf
  offsite. Ist sie verloren oder kompromittiert, legt der Operator eine neue Quelle an, richtet
  sie in der Wallet ein (neue Kennung) und entfernt dort die alte.
- Weder Schluessel noch Dateiinhalt werden ausgegeben oder geloggt (ADR 0021 I4; Test in
  ``tests/unit/test_kai_pay_bridge.py``).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


class KeyMissingError(FileNotFoundError):
    """Keine Schluesseldatei - die Quelle ist noch nicht angelegt."""


def create_key(path: str | Path) -> ec.EllipticCurvePrivateKey:
    """Legt einen neuen P-256-Schluessel an. ``FileExistsError``, wenn die Datei existiert."""
    p = Path(path).expanduser()
    p.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if sys.platform != "win32":  # mkdir aendert ein vorhandenes Verzeichnis nicht
        if p.parent.stat().st_uid != os.getuid():
            raise PermissionError("Schluesselverzeichnis gehoert einem anderen Nutzer")
        os.chmod(p.parent, 0o700)
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(pem)
        fh.flush()
        os.fsync(fh.fileno())
    if sys.platform != "win32":  # Verzeichniseintrag ebenfalls dauerhaft (Stromausfall nach init)
        dfd = os.open(p.parent, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    return key


def load_key(path: str | Path) -> ec.EllipticCurvePrivateKey:
    p = Path(path).expanduser()
    if not p.is_file():
        raise KeyMissingError(str(p))
    if sys.platform != "win32" and p.stat().st_mode & 0o077:
        raise ValueError("Schluesseldatei zu offen (0600 erwartet)")
    try:
        key = serialization.load_pem_private_key(p.read_bytes(), password=None)
    except (ValueError, TypeError):
        # Keine Ausnahme-Details weiterreichen: sie koennten Teile der Datei enthalten.
        raise ValueError("Schluesseldatei unlesbar") from None
    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
        raise ValueError("Schluesseldatei ist kein P-256-Schluessel")
    return key
