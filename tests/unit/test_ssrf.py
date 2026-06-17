"""Tests for the SSRF guard (app.security.ssrf).

Covers the two hardening fixes:
  * IPv4-mapped IPv6 literals (``::ffff:127.0.0.1``) and the property-based
    catch-all must be blocked — the old explicit-network list missed them.
  * ``ssrf_redirect_hook`` must re-validate redirect targets, closing the
    SSRF-via-redirect bypass that ``follow_redirects=True`` opened.
"""

from __future__ import annotations

import httpx
import pytest

from app.core.errors import SecurityError
from app.security.ssrf import is_safe_url, ssrf_redirect_hook, validate_url


@pytest.mark.parametrize(
    "url",
    [
        "http://[::ffff:127.0.0.1]/",  # IPv4-mapped loopback
        "http://[::ffff:169.254.169.254]/",  # IPv4-mapped cloud metadata
        "http://[::ffff:10.0.0.1]/",  # IPv4-mapped RFC1918
        "http://127.0.0.1/",  # plain loopback
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://10.0.0.1/",  # RFC1918
        "http://[::1]/",  # IPv6 loopback
        "ftp://example.com/",  # non-http scheme
        "http:///nohost",  # missing host
    ],
)
def test_blocked_urls(url: str) -> None:
    assert not is_safe_url(url)
    with pytest.raises(SecurityError):
        validate_url(url)


def test_public_host_allowed() -> None:
    # Public DNS name resolves to a global IP — must pass.
    assert is_safe_url("https://example.com/")


@pytest.mark.asyncio
async def test_redirect_hook_blocks_internal_target() -> None:
    """A 3xx Location pointing at an internal host aborts the redirect chain."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "evil.test":
            return httpx.Response(
                302, headers={"location": "http://169.254.169.254/latest/meta-data/"}
            )
        return httpx.Response(200, text="internal-secret-should-never-be-reached")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport,
        follow_redirects=True,
        event_hooks={"response": [ssrf_redirect_hook]},
    ) as client:
        with pytest.raises(SecurityError):
            await client.get("http://evil.test/start")


@pytest.mark.asyncio
async def test_redirect_hook_allows_public_redirect() -> None:
    """A redirect to another public host is permitted (e.g. http→https)."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "https://example.com/final"})
        return httpx.Response(200, text="ok")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport,
        follow_redirects=True,
        event_hooks={"response": [ssrf_redirect_hook]},
    ) as client:
        resp = await client.get("http://example.com/start")
        assert resp.status_code == 200
        assert resp.text == "ok"


@pytest.mark.asyncio
async def test_redirect_hook_ignores_non_redirect() -> None:
    """A plain 200 response is a no-op for the hook."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="ok")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport,
        event_hooks={"response": [ssrf_redirect_hook]},
    ) as client:
        resp = await client.get("http://example.com/")
        assert resp.status_code == 200
