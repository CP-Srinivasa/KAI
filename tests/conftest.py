import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """TestClient with auth disabled (APP_ENV=testing, no API key).

    Auth middleware is tested separately in test_auth.py with isolated
    FastAPI instances.  All other API tests use this fixture and expect
    unauthenticated access to work.
    """
    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("APP_API_KEY", "")
    return TestClient(create_app())
