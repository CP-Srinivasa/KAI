from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routers import ai_control


def test_endpunkte_liefern_vertraege(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    async def ohne_transport(**_kw: Any) -> None:
        return None

    monkeypatch.setattr(ai_control, "_transport", ohne_transport)
    monkeypatch.chdir(tmp_path)
    app = FastAPI()
    app.include_router(ai_control.router)
    client = TestClient(app)
    r = client.get("/dashboard/api/ai/control")
    assert r.status_code == 200 and r.json()["schema"] == "ai-control/v1"
    assert r.headers["cache-control"].startswith("no-store")
    h = client.get("/dashboard/api/ai/control/history?days=3")
    assert h.status_code == 200 and len(h.json()["days"]) == 3
    assert client.get("/dashboard/api/ai/control/history?days=99").status_code == 422


def test_eingebunden_mit_der_dashboard_regel(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Dashboard-Regel (D-124/D-156d): lokal offen, ueber den Tunnel nur mit CF-Access."""
    from app.api.main import create_app
    from app.core.settings import get_settings

    async def ohne_transport(**_kw: Any) -> None:
        return None

    monkeypatch.setattr(ai_control, "_transport", ohne_transport)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("APP_API_KEY", "geheim-test-key-0123456789")
    monkeypatch.setenv("APP_CF_ACCESS_ALLOWED_EMAILS", "operator@example.org")
    get_settings.cache_clear()
    try:
        client = TestClient(create_app())
        lokal = client.get("/dashboard/api/ai/control")
        tunnel = client.get(
            "/dashboard/api/ai/control",
            headers={"Cf-Ray": "x", "Cf-Connecting-IP": "203.0.113.9"},
        )
        erlaubt = client.get(
            "/dashboard/api/ai/control",
            headers={
                "Cf-Ray": "x",
                "Cf-Connecting-IP": "203.0.113.9",
                "Cf-Access-Authenticated-User-Email": "operator@example.org",
            },
        )
    finally:
        get_settings.cache_clear()
    assert lokal.status_code == 200 and lokal.json()["schema"] == "ai-control/v1"
    assert tunnel.status_code in (401, 403), "Tunnel ohne CF-Access abgewiesen"
    assert erlaubt.status_code == 200
