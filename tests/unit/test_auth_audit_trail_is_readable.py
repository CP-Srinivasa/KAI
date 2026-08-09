"""Der Auth-Audit-Trail muss lesbar sein, nicht nur vorhanden.

``_audit_access`` schrieb über den stdlib-Logger mit ``extra={...}``:

    logger.info("auth_access", extra={"decision": ..., "path": ..., "client_ip": ...})

``app/core/logging.py`` konfiguriert aber ``logging.basicConfig(format="%(message)s")``,
und kein Handler im Repo rendert ``extra``. Jede Zeile in ``logs/server.log``
lautete deshalb wörtlich nur ``auth_access`` — granted und denied
ununterscheidbar, ohne Pfad, ohne IP, ohne Identität.

Nach einem Auth-Vorfall gäbe es damit keine Forensik. Bestehende Tests blieben
grün, weil ``caplog`` das Record-*Objekt* sieht und nicht die gerenderte Zeile —
genau die Lücke, durch die das ein Jahr überlebt hat.
"""

from __future__ import annotations

import logging

import pytest

from app.security import auth


class _Req:
    """Minimaler Request-Stub für den Audit-Pfad."""

    def __init__(self, path: str = "/dashboard/api/x", method: str = "GET") -> None:
        self.url = type("U", (), {"path": path})()
        self.method = method
        self.headers: dict[str, str] = {}
        self.client = type("C", (), {"host": "203.0.113.7"})()


def test_audit_zeile_traegt_die_entscheidung_im_text(caplog: pytest.LogCaptureFixture) -> None:
    """Die gerenderte Nachricht — nicht das Record-Objekt — muss den Befund tragen."""
    with caplog.at_level(logging.INFO):
        auth._audit_access(
            request=_Req(),
            decision="denied",
            reason="email_not_allowlisted",
            email="operator@example.com",
            status_code=403,
        )

    rendered = "\n".join(r.getMessage() for r in caplog.records)
    assert "denied" in rendered, (
        f"Die gerenderte Zeile unterscheidet granted nicht von denied — gerendert: {rendered!r}"
    )
    assert "email_not_allowlisted" in rendered
    assert "/dashboard/api/x" in rendered


def test_audit_zeile_traegt_pfad_und_ip(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO):
        auth._audit_access(
            request=_Req(path="/dashboard/api/ln/treasury"),
            decision="granted",
            reason="cf_access_email",
            email="operator@example.com",
            status_code=200,
        )

    rendered = "\n".join(r.getMessage() for r in caplog.records)
    assert "/dashboard/api/ln/treasury" in rendered
    assert "203.0.113.7" in rendered


def test_audit_zeile_enthaelt_niemals_die_klartext_email(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Korrelierbarer Fingerprint ja, PII nein."""
    with caplog.at_level(logging.INFO):
        auth._audit_access(
            request=_Req(),
            decision="granted",
            reason="cf_access_email",
            email="operator@example.com",
            status_code=200,
        )

    rendered = "\n".join(r.getMessage() for r in caplog.records)
    assert "operator@example.com" not in rendered
    assert "auth_access" in rendered
