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
    # P2: pretend tests run on the Pi so the off-Pi `probe_location` warning
    # (app.alerts.health_check) doesn't pollute unrelated assertions. Tests
    # that exercise the off-Pi path opt in via monkeypatch.setenv to a
    # non-matching marker.
    import socket as _socket

    _host = _socket.gethostname() or "test-host"
    monkeypatch.setenv("KAI_PI_HOSTNAME_MARKER", _host.lower())


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> None:
    """Clear the get_settings() lru_cache around every test (settings-cache fix).

    get_settings() is now process-cached (@lru_cache). Tests monkeypatch env per
    case (see _pin_feature_defaults + the `client` fixture); without clearing the
    cache the first cached AppSettings would leak across the whole session and
    ignore those per-test env overrides. Clear before AND after each test so each
    case resolves its own environment on the first get_settings() call.
    """
    from app.core.settings import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _reset_paper_engine_singleton() -> None:
    """Drop the PaperExecutionEngine singleton between tests (P1 #7).

    The 2026-05-14 singleton-refactor (``app.execution.paper_engine_singleton``)
    means one engine instance is reused across all consumers within a process.
    In production that is correct; in tests it would leak ``_filled_keys`` and
    portfolio state from one case into the next (Bridge tests open env-001 →
    next test re-fires env-001 → DuplicateOrderError). Clearing the cache
    before and after every test enforces deterministic per-test isolation.
    """
    from app.execution.paper_engine_singleton import reset_paper_engine_cache

    reset_paper_engine_cache()
    yield
    reset_paper_engine_cache()


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
