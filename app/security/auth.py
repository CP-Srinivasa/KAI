"""API authentication middleware.

Two accepted auth mechanisms (checked in order):

1. **Cloudflare Access** — browser traffic via the public tunnel. CF Access
   authenticates the user at the edge and forwards the request with the header
   ``Cf-Access-Authenticated-User-Email``. If that header's value appears in
   ``CF_ACCESS_ALLOWED_EMAILS`` (comma-separated), the request is trusted.

2. **Bearer token** — local scripts / cron / freshness-probe via ``127.0.0.1``.
   Must include ``Authorization: Bearer <APP_API_KEY>``.

When ``APP_API_KEY`` is empty the behaviour depends on the environment:
- development / dev / test / testing: auth disabled, warning logged once.
- all other environments: startup fails with ConfigurationError — fail-closed.

Threat model (why header-trust is sufficient, no JWT validation):
- Server binds ``127.0.0.1`` only; external traffic arrives exclusively via
  the cloudflared tunnel, which sits behind the CF Access policy.
- The CF-Access header cannot be spoofed by an external attacker — CF strips
  and re-sets it on every authenticated request.
- A local process could set the header, but a local process can also read
  ``.env`` directly; no additional attack surface is gained.
- Upgrade path to JWT-validation (via CF JWKS + AUD tag): validate
  ``Cf-Access-Jwt-Assertion`` against ``https://<team>.cloudflareaccess.com/cdn-cgi/access/certs``.

Usage (attached to FastAPI in app/api/main.py):
    from app.security.auth import setup_auth
    setup_auth(app, settings.api_key, settings.env)
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import time
from collections.abc import Awaitable, Callable, Iterable
from threading import Lock

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from app.core.errors import ConfigurationError

logger = logging.getLogger(__name__)


# SENTR-F-003: in-memory brute-force guard per client IP. Sliding window of
# failure timestamps; once >= threshold failures occur within the window,
# further requests from that IP are rejected with 429 until the window
# expires. Reset on any successful auth from the same IP.
#
# Scope: lives in-process. A restart clears the map — intentional, since
# prod restarts are rare and an attacker who can trigger a restart already
# owns more than the rate-limiter can defend against. Shared-nothing; no
# Redis/DB dependency.
_AUTH_FAILURES: dict[str, list[float]] = {}
_AUTH_FAILURES_LOCK = Lock()


def _prune_failures(ip: str, window: float, now: float) -> list[float]:
    """Drop failure timestamps older than ``window`` seconds. Returns kept."""
    cutoff = now - window
    with _AUTH_FAILURES_LOCK:
        kept = [ts for ts in _AUTH_FAILURES.get(ip, []) if ts >= cutoff]
        if kept:
            _AUTH_FAILURES[ip] = kept
        else:
            _AUTH_FAILURES.pop(ip, None)
    return kept


def _record_auth_failure(ip: str, window: float, now: float) -> int:
    """Append a failure for ``ip`` and return the in-window count."""
    if not ip:
        return 0
    with _AUTH_FAILURES_LOCK:
        bucket = _AUTH_FAILURES.setdefault(ip, [])
        cutoff = now - window
        bucket[:] = [ts for ts in bucket if ts >= cutoff]
        bucket.append(now)
        return len(bucket)


def _reset_auth_failures(ip: str) -> None:
    """Clear all failures for ``ip`` — called on successful auth."""
    if not ip:
        return
    with _AUTH_FAILURES_LOCK:
        _AUTH_FAILURES.pop(ip, None)


def _is_rate_limited(
    ip: str, threshold: int, window: float, now: float
) -> tuple[bool, int]:
    """Return (locked, retry_after_seconds).

    Locked when ``len(in-window failures) >= threshold``. ``retry_after``
    is the remaining time until the oldest in-window failure ages out —
    once it does, ``len`` drops below threshold and the IP can retry.
    """
    if not ip or threshold <= 0:
        return False, 0
    kept = _prune_failures(ip, window, now)
    if len(kept) < threshold:
        return False, 0
    oldest = kept[0]
    retry_after = max(1, int(oldest + window - now) + 1)
    return True, retry_after


def _reset_rate_limit_registry_for_tests() -> None:
    """Test-only helper — clears the module-level failure registry."""
    with _AUTH_FAILURES_LOCK:
        _AUTH_FAILURES.clear()


def _hash_email(email: str) -> str:
    """Return a short hash of the email — audit trail without persisting PII."""
    if not email:
        return ""
    return hashlib.sha256(email.encode("utf-8")).hexdigest()[:12]


def _client_ip(request: Request) -> str:
    """Best-effort client IP. Cf-Connecting-IP (tunnel), X-Forwarded-For, peer."""
    cf = request.headers.get("Cf-Connecting-IP", "").strip()
    if cf:
        return cf
    xff = request.headers.get("X-Forwarded-For", "").strip()
    if xff:
        return xff.split(",")[0].strip()
    if request.client is not None:
        return request.client.host
    return ""


def _audit_access(
    *,
    decision: str,
    reason: str,
    request: Request,
    email: str = "",
    status_code: int | None = None,
) -> None:
    """Write one structured access-audit line (SENTR-F-001).

    ``decision`` is ``granted`` or ``denied``. Email is hashed to a short
    fingerprint — enough for correlation across requests, never the full PII.
    """
    logger.info(
        "auth_access",
        extra={
            "decision": decision,
            "reason": reason,
            "path": request.url.path,
            "method": request.method,
            "client_ip": _client_ip(request),
            "email_hash": _hash_email(email),
            "status_code": status_code,
        },
    )

# Environments where an empty API key is acceptable (local dev / CI).
_DEV_TEST_ENVS: frozenset[str] = frozenset({"development", "dev", "test", "testing"})

_AUTH_DISABLED_WARNED = False


def setup_auth(
    app: FastAPI,
    api_key: str,
    env: str = "development",
    cf_allowed_emails: Iterable[str] = (),
    tv_webhook_enabled: bool = False,
    rate_limit_threshold: int = 5,
    rate_limit_window_seconds: float = 300.0,
    api_key_next: str = "",
) -> None:
    """Attach auth middleware (CF-Access + Bearer) to the FastAPI app.

    Args:
        app:                FastAPI application instance.
        api_key:            Value of APP_API_KEY.
        env:                Value of APP_ENV (default: ``"development"``).
        cf_allowed_emails:  Iterable of emails allowed to pass via the
                            ``Cf-Access-Authenticated-User-Email`` header.
        tv_webhook_enabled: Whether the TradingView webhook is configured.
                            When False, ``/tradingview/webhook`` is rejected
                            at the middleware layer as well — Defense-in-Depth
                            (SENTR-F-002) mirroring the router-level 404 gate
                            so an accidental router-gate removal cannot open
                            the endpoint.
        rate_limit_threshold: SENTR-F-003. Maximum in-window failed-auth
                              attempts per client IP before 429 responses
                              begin.  Set to 0 to disable.
        rate_limit_window_seconds: Sliding-window duration for the failure
                                   counter.  Once the oldest in-window
                                   failure ages out, the IP can retry.
        api_key_next: SENTR-F-008. Optional second Bearer accepted during
                      zero-downtime rotation. Empty string = single-key mode.

    Raises:
        ConfigurationError: if ``api_key`` is empty outside dev/test environments.
    """
    global _AUTH_DISABLED_WARNED

    if not api_key:
        if env.lower() not in _DEV_TEST_ENVS:
            raise ConfigurationError(
                f"APP_API_KEY is required in env='{env}'. "
                "Authentication cannot be disabled outside development/test contexts. "
                "Set APP_API_KEY in your environment."
            )
        if not _AUTH_DISABLED_WARNED:
            logger.warning(
                "API authentication is DISABLED. "
                "Set APP_API_KEY in your environment to protect all endpoints."
            )
            _AUTH_DISABLED_WARNED = True
        return  # no middleware attached

    cf_allowed = frozenset(e.strip().lower() for e in cf_allowed_emails if e and e.strip())
    # SENTR-F-008: rotation window. Both keys are checked with compare_digest.
    # Empty next-key stays empty — compare_digest on "" never succeeds, so the
    # single-key case requires no branching.
    next_key = api_key_next or ""

    @app.middleware("http")
    async def _auth_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Public read-only endpoints:
        # - /health for infra checks
        # - /tradingview/webhook (D-125 TV-1+): external webhook sender
        #   (TradingView) cannot attach a Bearer header; the endpoint has
        #   its own HMAC / shared-token auth + fail-closed 404 gating.
        path = request.url.path.rstrip("/")

        # SENTR-F-002: Defense-in-Depth — middleware mirrors the router
        # 404-gate for /tradingview/webhook when the feature is disabled.
        # The router already returns 404 via _settings_gate; this is a
        # second ring so a future accidental removal (or a new router
        # that forgets the gate) cannot open the endpoint silently.
        if path == "/tradingview/webhook" and not tv_webhook_enabled:
            _audit_access(
                decision="denied",
                reason="tv_webhook_disabled",
                request=request,
                status_code=404,
            )
            return JSONResponse(status_code=404, content={"detail": "Not Found"})

        if path in ("", "/health", "/tradingview/webhook"):
            _audit_access(decision="granted", reason="public", request=request)
            return await call_next(request)

        # SENTR-F-003: brute-force guard. Check BEFORE any auth decision so a
        # locked IP cannot continue to generate failure counts or probe timing
        # differences. Public paths above are exempt (no auth → no failures).
        client_ip = _client_ip(request)
        now = time.monotonic()
        locked, retry_after = _is_rate_limited(
            client_ip, rate_limit_threshold, rate_limit_window_seconds, now
        )
        if locked:
            _audit_access(
                decision="denied",
                reason="rate_limited",
                request=request,
                status_code=429,
            )
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many failed authentication attempts"},
                headers={"Retry-After": str(retry_after)},
            )

        # /dashboard/* (D-124 dashboard HTML + /dashboard/api/*):
        # Defense-in-Depth (D-156d). The primary gate is Cloudflare Access
        # at the edge — but a CF-Access policy misconfiguration would
        # leave /dashboard/* wide-open on kai-trader.org otherwise.
        #
        # Rule: tunnel traffic (Cf-Ray header present, set by Cloudflare
        # edge, un-spoofable from outside because the server binds to
        # 127.0.0.1 only) MUST additionally carry an allowlisted
        # Cf-Access-Authenticated-User-Email. Local traffic (no Cf-Ray)
        # stays open for operator scripts / Vite dev proxy / cron probes.
        # Dev environments without a cf_allowed list also stay open —
        # otherwise the operator would lock themselves out locally.
        # NEO-F-004: exact "/dashboard" or "/dashboard/*" only — prevent a
        # future "/dashboardv2"-style route from silently inheriting the
        # dashboard-defense-in-depth policy instead of Bearer auth.
        if path == "/dashboard" or path.startswith("/dashboard/"):
            if not cf_allowed or not request.headers.get("Cf-Ray"):
                _reset_auth_failures(client_ip)
                _audit_access(
                    decision="granted", reason="dashboard_local", request=request
                )
                return await call_next(request)
            # NEO-P-001 (A): Cloudflare sets both Cf-Ray AND Cf-Connecting-IP
            # on every edge-authenticated request. A non-CF reverse proxy that
            # only forwards Cf-Ray (without Cf-Connecting-IP) is suspicious —
            # either a misconfigured front-end or an attempted spoof. Fail
            # closed so a future deployment change (nginx/caddy in front,
            # cloudflared → Tailscale Funnel migration) surfaces as 401 at
            # monitoring rather than as a silently-downgraded trust boundary.
            if not request.headers.get("Cf-Connecting-IP"):
                _record_auth_failure(client_ip, rate_limit_window_seconds, now)
                _audit_access(
                    decision="denied",
                    reason="dashboard_cf_ray_orphan",
                    request=request,
                    status_code=401,
                )
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Cloudflare Access authentication required"},
                )
            cf_email = (
                request.headers.get("Cf-Access-Authenticated-User-Email", "")
                .strip()
                .lower()
            )
            if cf_email and cf_email in cf_allowed:
                _reset_auth_failures(client_ip)
                _audit_access(
                    decision="granted",
                    reason="dashboard_cf_access",
                    request=request,
                    email=cf_email,
                )
                return await call_next(request)
            _record_auth_failure(client_ip, rate_limit_window_seconds, now)
            _audit_access(
                decision="denied",
                reason="dashboard_cf_access_missing",
                request=request,
                email=cf_email,
                status_code=401,
            )
            return JSONResponse(
                status_code=401,
                content={"detail": "Cloudflare Access authentication required"},
            )

        # (1) Cloudflare Access: trust if email header matches allowlist.
        if cf_allowed:
            cf_email = request.headers.get("Cf-Access-Authenticated-User-Email", "").strip().lower()
            if cf_email and cf_email in cf_allowed:
                _reset_auth_failures(client_ip)
                _audit_access(
                    decision="granted",
                    reason="cf_access",
                    request=request,
                    email=cf_email,
                )
                return await call_next(request)

        # (2) Bearer token: required for local scripts/cron.
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[len("Bearer ") :]
            if secrets.compare_digest(token, api_key):
                _reset_auth_failures(client_ip)
                _audit_access(decision="granted", reason="bearer", request=request)
                return await call_next(request)
            if next_key and secrets.compare_digest(token, next_key):
                _reset_auth_failures(client_ip)
                _audit_access(
                    decision="granted", reason="bearer_next", request=request
                )
                return await call_next(request)
            _record_auth_failure(client_ip, rate_limit_window_seconds, now)
            _audit_access(
                decision="denied",
                reason="bearer_invalid",
                request=request,
                status_code=403,
            )
            return JSONResponse(
                status_code=403,
                content={"detail": "Invalid API key"},
            )

        _record_auth_failure(client_ip, rate_limit_window_seconds, now)
        _audit_access(
            decision="denied",
            reason="missing_authorization",
            request=request,
            status_code=401,
        )
        return JSONResponse(
            status_code=401,
            content={"detail": "Missing Authorization header"},
            headers={"WWW-Authenticate": "Bearer"},
        )

    rotation_suffix = " + rotation-next" if next_key else ""
    if cf_allowed:
        logger.info(
            "API authentication enabled — CF-Access (emails=%d) + Bearer token%s",
            len(cf_allowed),
            rotation_suffix,
        )
    else:
        logger.info(
            "API authentication enabled — Bearer token required%s", rotation_suffix
        )
