"""GET /health/config — the running configuration is provable, secrets never leave."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.api import main as api_main
from app.core.settings import AppSettings, get_settings

_SECRET = "sk-live-config-endpoint-9f8e7d6c"


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("OPENAI_API_KEY", _SECRET)
    monkeypatch.setenv("EXECUTION_ENTRY_MODE", "paper")
    monkeypatch.delenv("APP_API_KEY", raising=False)
    settings = AppSettings(_env_file=None)
    monkeypatch.setattr(api_main, "validate_secrets", lambda _s: None)
    monkeypatch.setattr(api_main, "get_settings", lambda: settings)
    app = api_main.create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app)


def test_health_config_returns_redacted_snapshot(client: TestClient) -> None:
    resp = client.get("/health/config")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {"sections", "explicit"}
    assert body["sections"]["app"]["env"] == "testing"
    assert body["sections"]["execution"]["entry_mode"] == "paper"
    assert body["sections"]["providers"]["openai_api_key"].startswith("(set:")
    assert "openai_api_key" in body["explicit"]["providers"]


def test_health_config_never_leaks_secret_values(client: TestClient) -> None:
    blob = json.dumps(client.get("/health/config").json())
    assert _SECRET not in blob
    assert "sk-live" not in blob


def test_timestamp_job_capacity_health_is_read_only_and_uncached(
    client: TestClient,
) -> None:
    store = MagicMock()
    store.capacity_snapshot.return_value = {
        "state": "warning",
        "used": 8,
        "max": 10,
        "available": 2,
        "utilization": 0.8,
    }
    with patch("app.integrity.timestamp_jobs.TimestampJobStore", return_value=store):
        response = client.get("/health/integrity/timestamp-jobs")
    assert response.status_code == 200
    assert response.json() == store.capacity_snapshot.return_value
    assert response.headers["Cache-Control"].startswith("no-store")


def test_timestamp_job_capacity_health_is_not_public() -> None:
    from app.security import auth

    source = Path(auth.__file__).read_text(encoding="utf-8")
    public_line = [line for line in source.splitlines() if 'if path in ("", "/health"' in line]
    assert public_line
    assert "/health/integrity/timestamp-jobs" not in public_line[0]
