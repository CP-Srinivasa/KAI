"""Tests for SENTR-F-003 — brute-force guard on the auth middleware."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.security.auth import (
    _record_auth_failure,
    _reset_rate_limit_registry_for_tests,
    setup_auth,
)


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    _reset_rate_limit_registry_for_tests()
    yield
    _reset_rate_limit_registry_for_tests()


def _app(api_key: str = "secret", threshold: int = 3, window: float = 60.0) -> FastAPI:
    app = FastAPI()

    @app.get("/protected")
    async def _protected() -> dict[str, str]:
        return {"ok": "true"}

    setup_auth(
        app,
        api_key=api_key,
        env="production",
        rate_limit_threshold=threshold,
        rate_limit_window_seconds=window,
    )
    return app


def test_below_threshold_still_returns_auth_error_not_429() -> None:
    client = TestClient(_app(threshold=3))
    for _ in range(2):
        r = client.get("/protected", headers={"Authorization": "Bearer wrong"})
        assert r.status_code == 403


def test_threshold_exceeded_returns_429_with_retry_after() -> None:
    client = TestClient(_app(threshold=3))
    # Three failures trip the counter to threshold.
    for _ in range(3):
        client.get("/protected", headers={"Authorization": "Bearer wrong"})
    # Fourth attempt must be rate-limited regardless of credentials supplied.
    r = client.get("/protected", headers={"Authorization": "Bearer secret"})
    assert r.status_code == 429
    assert "Retry-After" in r.headers
    assert int(r.headers["Retry-After"]) >= 1


def test_threshold_zero_disables_guard() -> None:
    client = TestClient(_app(threshold=0))
    for _ in range(10):
        r = client.get("/protected", headers={"Authorization": "Bearer wrong"})
        # 403 stays 403; guard never fires.
        assert r.status_code == 403


def test_successful_auth_resets_failure_counter() -> None:
    client = TestClient(_app(threshold=3))
    # Two failures — one below threshold.
    for _ in range(2):
        client.get("/protected", headers={"Authorization": "Bearer wrong"})
    # Success resets counter.
    r_ok = client.get("/protected", headers={"Authorization": "Bearer secret"})
    assert r_ok.status_code == 200
    # Two more failures should NOT trip the guard — counter was cleared.
    for _ in range(2):
        r = client.get("/protected", headers={"Authorization": "Bearer wrong"})
        assert r.status_code == 403


def test_missing_auth_header_also_counts_as_failure() -> None:
    client = TestClient(_app(threshold=2))
    # Two 401s (missing header) — each is counted.
    for _ in range(2):
        r = client.get("/protected")
        assert r.status_code == 401
    # Third attempt gets rate-limited.
    r = client.get("/protected", headers={"Authorization": "Bearer secret"})
    assert r.status_code == 429


def test_health_endpoint_never_rate_limited() -> None:
    client = TestClient(_app(threshold=1))
    # Simulate prior failures from this IP.
    _record_auth_failure("testclient", 60.0, 0.0)
    _record_auth_failure("testclient", 60.0, 0.0)
    # /health bypasses all auth and rate-limit logic.
    r = client.get("/health")
    assert r.status_code == 404  # no /health route in this mini-app
    # Point is: it didn't come back as 429 from the middleware.
    assert r.status_code != 429


def test_window_expiry_allows_retry_after_aging_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failure older than the window must not count toward the threshold."""
    import app.security.auth as auth_mod

    fake_time = {"now": 1000.0}

    def _now() -> float:
        return fake_time["now"]

    monkeypatch.setattr(auth_mod.time, "monotonic", _now)

    client = TestClient(_app(threshold=2, window=30.0))
    # Two failures at t=1000.
    for _ in range(2):
        client.get("/protected", headers={"Authorization": "Bearer wrong"})
    # Immediately after — locked.
    r_locked = client.get("/protected", headers={"Authorization": "Bearer secret"})
    assert r_locked.status_code == 429

    # Advance well past the window — the old failures age out.
    fake_time["now"] = 1100.0
    r_ok = client.get("/protected", headers={"Authorization": "Bearer secret"})
    assert r_ok.status_code == 200
