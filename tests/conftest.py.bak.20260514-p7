import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app


@pytest.fixture(autouse=True)
def _pin_feature_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force feature-flag defaults for the whole suite (D-184 hygiene).

    The repo ships a local ``.env`` for operator convenience (e.g. with
    ``EXECUTION_PAPER_MIN_PRIORITY=10`` to activate the D-182 gate). Tests
    must never depend on that ambient state — they verify code behaviour
    against the documented *default*, not the operator's current toggle.
    Individual tests that want a different state opt in via their own
    ``monkeypatch.setenv`` (function scope wins over earlier setenv calls
    on the same key).
    """
    monkeypatch.setenv("EXECUTION_PAPER_MIN_PRIORITY", "1")


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
