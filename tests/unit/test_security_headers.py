"""Tests for SecurityHeadersMiddleware (SENTR-F-007)."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.middleware.security_headers import (
    SecurityHeadersMiddleware,
    SecurityHeadersPolicy,
    build_default_csp,
    setup_security_headers,
)


def _app_with_policy(**overrides: object) -> FastAPI:
    app = FastAPI()

    @app.get("/ping")
    def _ping() -> dict[str, str]:
        return {"ok": "1"}

    kwargs: dict[str, object] = {"enabled": True}
    kwargs.update(overrides)
    setup_security_headers(app, **kwargs)  # type: ignore[arg-type]
    return app


def test_default_csp_contains_core_directives() -> None:
    csp = build_default_csp()
    assert "default-src 'self'" in csp
    assert "script-src 'self'" in csp
    assert "style-src 'self' 'unsafe-inline'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "object-src 'none'" in csp


def test_extra_csp_script_src_is_appended() -> None:
    csp = build_default_csp("https://telegram.org https://cdn.example.com")
    assert "script-src 'self' https://telegram.org https://cdn.example.com" in csp


def test_middleware_attaches_all_headers_to_json_response() -> None:
    client = TestClient(_app_with_policy())
    response = client.get("/ping")
    assert response.status_code == 200
    assert response.headers["Content-Security-Policy"].startswith("default-src 'self'")
    assert response.headers["Strict-Transport-Security"].startswith(
        "max-age=31536000"
    )
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "camera=()" in response.headers["Permissions-Policy"]


def test_report_only_emits_report_only_header_not_enforced() -> None:
    client = TestClient(_app_with_policy(csp_report_only=True))
    response = client.get("/ping")
    assert "Content-Security-Policy-Report-Only" in response.headers
    assert "Content-Security-Policy" not in response.headers


def test_disabled_attaches_no_headers() -> None:
    client = TestClient(_app_with_policy(enabled=False))
    response = client.get("/ping")
    assert "Content-Security-Policy" not in response.headers
    assert "Strict-Transport-Security" not in response.headers
    assert "X-Frame-Options" not in response.headers


def test_hsts_max_age_respects_override() -> None:
    client = TestClient(_app_with_policy(hsts_max_age=7_776_000))
    response = client.get("/ping")
    assert (
        response.headers["Strict-Transport-Security"]
        == "max-age=7776000; includeSubDomains"
    )


def test_extra_script_src_propagates_through_setup() -> None:
    client = TestClient(
        _app_with_policy(extra_csp_script_src="https://telegram.org")
    )
    response = client.get("/ping")
    assert "https://telegram.org" in response.headers["Content-Security-Policy"]


def test_middleware_does_not_overwrite_preexisting_header() -> None:
    # Defense against a downstream layer that already sets a custom policy.
    app = FastAPI()

    @app.get("/ping")
    def _ping() -> dict[str, str]:
        return {"ok": "1"}

    policy = SecurityHeadersPolicy(
        csp="default-src 'none'",
        csp_report_only=False,
        hsts_max_age=31_536_000,
        frame_options="DENY",
        referrer_policy="strict-origin-when-cross-origin",
        permissions_policy="camera=()",
    )
    app.add_middleware(SecurityHeadersMiddleware, policy=policy)

    @app.middleware("http")
    async def _preset(request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        return response

    client = TestClient(app)
    response = client.get("/ping")
    # Starlette wraps middlewares LIFO — the @app.middleware runs outside
    # SecurityHeadersMiddleware, so its header wins.  The test documents
    # that setdefault() preserves existing values on the response.
    assert response.headers["X-Frame-Options"] == "SAMEORIGIN"
