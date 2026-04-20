"""Security-headers middleware (SENTR-F-007 / D-156g).

Defense-in-depth for the dashboard SPA and JSON endpoints when the
Cloudflare edge is not in front (direct-path Pi setup, local dev, or a
misconfigured tunnel). Cloudflare sets HSTS at the edge, but nothing
inside the app currently emits CSP, X-Frame-Options, Referrer-Policy,
Permissions-Policy, or X-Content-Type-Options.

Scope:
- Headers are attached to every response (static SPA, JSON, redirects).
  Browsers only act on headers relevant to the response type (CSP applies
  in HTML contexts, HSTS on HTTPS) — duplicating on JSON is cheap and
  prevents asymmetric gaps when routing changes.
- Settings-gated: ``security_headers_enabled`` default True.
- CSP defaults allow the React bundle (inline styles for Tailwind, data
  URIs for icon fonts) but block framing and remote scripts. The
  Telegram Mini App is rendered in a native WebView, not an iframe —
  ``frame-ancestors 'none'`` does not block that surface.
- Optional ``security_headers_extra_csp_script_src`` lets an operator
  allowlist additional script origins (e.g. a future CDN) without
  touching code.
- Report-only mode (``security_headers_csp_report_only``) emits the CSP
  under ``Content-Security-Policy-Report-Only`` — used for a safe
  rollout before enforcing.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response


@dataclass(frozen=True)
class SecurityHeadersPolicy:
    """Immutable policy bundle for the security-headers middleware."""

    csp: str
    csp_report_only: bool
    hsts_max_age: int
    frame_options: str
    referrer_policy: str
    permissions_policy: str


def build_default_csp(extra_script_src: str = "") -> str:
    """Return the default CSP string used when no override is configured.

    The policy is tight: self-only scripts/connects, inline styles allowed
    (required by Tailwind + inline SVG), data: URIs for images and fonts,
    no framing, no object/embed, no base-tag hijack.
    """

    script_src = "'self'"
    extra = extra_script_src.strip()
    if extra:
        script_src = f"'self' {extra}"
    directives = [
        "default-src 'self'",
        f"script-src {script_src}",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob:",
        "font-src 'self' data:",
        "connect-src 'self'",
        "frame-ancestors 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "object-src 'none'",
    ]
    return "; ".join(directives)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach a fixed bundle of security headers to every response."""

    def __init__(self, app: FastAPI, policy: SecurityHeadersPolicy) -> None:
        super().__init__(app)
        self._policy = policy

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        response = await call_next(request)
        policy = self._policy

        csp_header = (
            "Content-Security-Policy-Report-Only"
            if policy.csp_report_only
            else "Content-Security-Policy"
        )
        response.headers.setdefault(csp_header, policy.csp)
        response.headers.setdefault(
            "Strict-Transport-Security",
            f"max-age={policy.hsts_max_age}; includeSubDomains",
        )
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", policy.frame_options)
        response.headers.setdefault("Referrer-Policy", policy.referrer_policy)
        response.headers.setdefault("Permissions-Policy", policy.permissions_policy)
        return response


def setup_security_headers(
    app: FastAPI,
    *,
    enabled: bool,
    csp: str | None = None,
    csp_report_only: bool = False,
    hsts_max_age: int = 31_536_000,
    frame_options: str = "DENY",
    referrer_policy: str = "strict-origin-when-cross-origin",
    permissions_policy: str = (
        "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
    ),
    extra_csp_script_src: str = "",
) -> None:
    """Attach SecurityHeadersMiddleware when ``enabled`` is True.

    Kept as a function (mirrors ``setup_auth``) so settings stay in
    ``main.py`` and tests can opt out via monkeypatch without wiring.
    """

    if not enabled:
        return
    policy = SecurityHeadersPolicy(
        csp=csp if csp is not None else build_default_csp(extra_csp_script_src),
        csp_report_only=csp_report_only,
        hsts_max_age=hsts_max_age,
        frame_options=frame_options,
        referrer_policy=referrer_policy,
        permissions_policy=permissions_policy,
    )
    app.add_middleware(SecurityHeadersMiddleware, policy=policy)
