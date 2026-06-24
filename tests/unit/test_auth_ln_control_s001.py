"""S-001 — the LN value-layer control surface must NOT be reachable via the
unauthenticated ``dashboard_local`` bypass.

``/dashboard/api/ln/value-action`` mints invoices / plan-executes spends; a local
no-auth bypass there is an auth hole (SSRF / a Cf-Ray-stripping proxy / a local
process). It must require REAL auth (CF-Access email OR Bearer) even for local
traffic. Read-only dashboard paths keep the local-bypass convenience (F-002).
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.security.auth import (
    _client_ip,
    _reset_rate_limit_registry_for_tests,
    setup_auth,
)

_TUNNEL = {"Cf-Ray": "abc-FRA", "Cf-Connecting-IP": "203.0.113.7"}


@pytest.fixture(autouse=True)
def _isolate_rate_limit() -> object:
    """These tests now produce real 401s (the S-001 fix), which record brute-force
    failures on the shared socket-peer IP. Reset the process-global registry around
    each test so they neither lock each other out nor leak into other test files."""
    _reset_rate_limit_registry_for_tests()
    yield
    _reset_rate_limit_registry_for_tests()


def _app(*, cf_allowed: list[str] | None = None) -> FastAPI:
    app = FastAPI()

    @app.post("/dashboard/api/ln/value-action")
    async def _value_action() -> dict[str, str]:
        return {"ok": "true"}

    @app.get("/dashboard/api/ln/demand")
    async def _demand() -> dict[str, str]:
        return {"verdict": "NO-PASS"}

    @app.get("/dashboard/api/ping")
    async def _ping() -> dict[str, str]:
        return {"ok": "true"}

    setup_auth(app, api_key="secret", env="production", cf_allowed_emails=cf_allowed or [])
    return app


def test_value_action_local_without_auth_is_rejected() -> None:
    """S-001 core: local (no Cf-Ray) value-action must NOT pass via dashboard_local."""
    with TestClient(_app(cf_allowed=["ops@example.com"])) as client:
        r = client.post("/dashboard/api/ln/value-action", json={})
    assert r.status_code == 401


def test_value_action_local_without_auth_rejected_even_without_cf_allowlist() -> None:
    """The other local-bypass trigger (no cf_allowed configured) must ALSO not open it."""
    with TestClient(_app(cf_allowed=[])) as client:
        r = client.post("/dashboard/api/ln/value-action", json={})
    assert r.status_code == 401


def test_value_action_local_with_bearer_is_accepted() -> None:
    with TestClient(_app(cf_allowed=["ops@example.com"])) as client:
        r = client.post(
            "/dashboard/api/ln/value-action", json={}, headers={"Authorization": "Bearer secret"}
        )
    assert r.status_code == 200


def test_value_action_tunnel_with_allowed_email_is_accepted() -> None:
    with TestClient(_app(cf_allowed=["ops@example.com"])) as client:
        r = client.post(
            "/dashboard/api/ln/value-action",
            json={},
            headers={**_TUNNEL, "Cf-Access-Authenticated-User-Email": "ops@example.com"},
        )
    assert r.status_code == 200


def test_value_action_tunnel_without_email_is_rejected() -> None:
    with TestClient(_app(cf_allowed=["ops@example.com"])) as client:
        r = client.post("/dashboard/api/ln/value-action", json={}, headers=_TUNNEL)
    assert r.status_code == 401


def test_readonly_dashboard_local_bypass_preserved() -> None:
    """Regression: non-sensitive dashboard reads keep the local-bypass convenience."""
    with TestClient(_app(cf_allowed=["ops@example.com"])) as client:
        assert client.get("/dashboard/api/ping").status_code == 200
        assert client.get("/dashboard/api/ln/demand").status_code == 200


def test_unknown_ln_endpoint_requires_auth_fail_closed() -> None:
    """satoshi auflage 1: a NOT-allowlisted /dashboard/api/ln/* path requires auth even
    locally — a FUTURE LN mutation cannot silently inherit the bypass (fail-closed). The
    middleware auth decision precedes routing, so this is 401, not a no-auth 404."""
    with TestClient(_app(cf_allowed=["ops@example.com"])) as client:
        assert client.post("/dashboard/api/ln/payout", json={}).status_code == 401


def test_path_normalization_does_not_bypass_strong_auth() -> None:
    """satoshi auflage 4: percent-encoding / traversal that resolves to value-action
    must still require auth — the middleware path-view matches the routing path-view."""
    with TestClient(_app(cf_allowed=["ops@example.com"])) as client:
        for p in (
            "/dashboard/api/ln/%76alue-action",  # %76 == 'v'
            "/dashboard/api/ln/x/../value-action",
        ):
            assert client.post(p, json={}).status_code == 401, p


def _req(headers: dict[str, str], client_host: str = "127.0.0.1") -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "headers": raw, "client": (client_host, 1)})


def test_client_ip_ignores_spoofable_xff_in_local_path() -> None:
    """satoshi auflage 2: X-Forwarded-For must NOT key rate-limiting locally (spoofable
    → operator-IP lockout / lockout-dodge). Local path falls back to the socket peer."""
    assert _client_ip(_req({"X-Forwarded-For": "9.9.9.9"}, client_host="127.0.0.1")) == "127.0.0.1"


def test_client_ip_trusts_cf_connecting_ip() -> None:
    assert _client_ip(_req({"Cf-Connecting-IP": "203.0.113.7"})) == "203.0.113.7"
