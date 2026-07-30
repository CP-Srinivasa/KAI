"""Übertragungsgröße: Text-Antworten müssen komprimiert rausgehen.

Operator-Befund 2026-07-30: der SPA-Build ging unkomprimiert über die Leitung
(~520 kB kritischer Pfad) und jeder Dashboard-Poll schickte sein JSON roh, weil
alle Endpoints ``no-store`` setzen. Diese Tests halten die Kompression fest —
sonst fällt sie beim nächsten Middleware-Umbau lautlos wieder heraus.
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.api.main import create_app


def _client() -> TestClient:
    # GZip greift nur, wenn der Client es anbietet; TestClient dekomprimiert
    # transparent, darum wird der Header geprüft, nicht der Body.
    return TestClient(create_app())


def test_large_json_response_is_gzipped() -> None:
    """Eine Antwort über der Mindestgröße kommt mit content-encoding: gzip."""
    r = _client().get("/openapi.json", headers={"Accept-Encoding": "gzip"})

    if r.status_code == 404:  # in production sind die Docs abgeschaltet
        return
    assert r.headers.get("content-encoding") == "gzip"
    # Body muss weiterhin valides JSON sein (Dekompression verlustfrei).
    assert isinstance(json.loads(r.text), dict)


def test_small_response_is_not_gzipped() -> None:
    """Unter der Mindestgröße bleibt die Antwort unangetastet (kein Overhead)."""
    r = _client().get("/health", headers={"Accept-Encoding": "gzip"})

    assert r.status_code == 200
    assert "content-encoding" not in {k.lower() for k in r.headers}


def test_client_without_gzip_support_gets_plain_bytes() -> None:
    """Ohne Accept-Encoding wird NICHT komprimiert — kein kaputter Client."""
    r = _client().get("/openapi.json", headers={"Accept-Encoding": "identity"})

    if r.status_code == 404:
        return
    assert r.headers.get("content-encoding") != "gzip"
    assert isinstance(json.loads(r.text), dict)


def test_security_headers_survive_compression() -> None:
    """Security-Header bleiben äußerste Schicht — Kompression darf sie nicht schlucken.

    Regressionsschutz für die Middleware-Reihenfolge (GZip innen, Header außen).
    """
    r = _client().get("/openapi.json", headers={"Accept-Encoding": "gzip"})

    if r.status_code == 404:
        return
    lower = {k.lower() for k in r.headers}
    assert "x-content-type-options" in lower
    assert "x-frame-options" in lower
