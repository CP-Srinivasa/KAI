"""Tests for SENTR-F-008 — zero-downtime APP_API_KEY rotation."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.security.auth import _reset_rate_limit_registry_for_tests, setup_auth


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    _reset_rate_limit_registry_for_tests()
    yield
    _reset_rate_limit_registry_for_tests()


def _app(current: str, next_key: str = "") -> FastAPI:
    app = FastAPI()

    @app.get("/protected")
    async def _protected() -> dict[str, str]:
        return {"ok": "true"}

    setup_auth(
        app,
        api_key=current,
        env="production",
        api_key_next=next_key,
        rate_limit_threshold=0,  # disable rate-limit so we can hammer the endpoint
    )
    return app


def test_current_key_accepted_when_next_set() -> None:
    client = TestClient(_app("old-key", "new-key"))
    r = client.get("/protected", headers={"Authorization": "Bearer old-key"})
    assert r.status_code == 200


def test_next_key_accepted_when_configured() -> None:
    client = TestClient(_app("old-key", "new-key"))
    r = client.get("/protected", headers={"Authorization": "Bearer new-key"})
    assert r.status_code == 200


def test_wrong_key_still_rejected_in_rotation_window() -> None:
    client = TestClient(_app("old-key", "new-key"))
    r = client.get("/protected", headers={"Authorization": "Bearer garbage"})
    assert r.status_code == 403


def test_empty_next_key_is_single_key_mode() -> None:
    # Empty string must NOT accidentally authenticate an empty Bearer token.
    client = TestClient(_app("old-key", ""))
    r = client.get("/protected", headers={"Authorization": "Bearer "})
    assert r.status_code == 403


def test_rotation_only_accepts_exact_match_not_substring() -> None:
    # secrets.compare_digest is constant-time AND exact; guard against a
    # future refactor that uses `in` or startswith.
    client = TestClient(_app("old-key", "new-key-longer"))
    r = client.get("/protected", headers={"Authorization": "Bearer new-key"})
    assert r.status_code == 403


def test_after_rotation_completed_old_key_dead() -> None:
    # Operator promoted new→current, cleared next.
    client = TestClient(_app("new-key", ""))
    r_new = client.get("/protected", headers={"Authorization": "Bearer new-key"})
    assert r_new.status_code == 200
    r_old = client.get("/protected", headers={"Authorization": "Bearer old-key"})
    assert r_old.status_code == 403
