"""SSRF (Server-Side Request Forgery) protection.

Every outbound HTTP request from ingestion modules MUST call validate_url()
before opening a connection.

Blocked:
- Non-http/https schemes (file://, ftp://, gopher://, etc.)
- Private IPv4 ranges (RFC 1918): 10/8, 172.16/12, 192.168/16
- Loopback: 127.0.0.0/8, ::1
- Link-local / cloud metadata: 169.254.0.0/16 (AWS, GCP, Azure metadata)
- Multicast: 224.0.0.0/4
- IPv4-mapped IPv6 (``::ffff:127.0.0.1``) and other non-global IPv6 — the
  mapped IPv4 is unwrapped and re-checked, and a property catch-all blocks
  any private/loopback/link-local/reserved/multicast/unspecified address that
  is not in the explicit network lists.
- Missing or empty host

Usage:
    from app.security.ssrf import validate_url, ssrf_redirect_hook
    validate_url(url)          # raises SecurityError on violation

    # When following redirects, re-validate every hop — the initial-URL check
    # alone is bypassed by a 3xx Location pointing at an internal host:
    async with httpx.AsyncClient(
        follow_redirects=True,
        event_hooks={"response": [ssrf_redirect_hook]},
    ) as client:
        ...
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Any
from urllib.parse import urlparse

from app.core.errors import SecurityError

__all__ = ["validate_url", "is_safe_url", "ssrf_redirect_hook", "SecurityError"]

# Private and reserved IPv4 networks — never reachable from the internet
_BLOCKED_NETWORKS_V4: tuple[ipaddress.IPv4Network, ...] = (
    ipaddress.IPv4Network("10.0.0.0/8"),  # RFC 1918 private
    ipaddress.IPv4Network("172.16.0.0/12"),  # RFC 1918 private
    ipaddress.IPv4Network("192.168.0.0/16"),  # RFC 1918 private
    ipaddress.IPv4Network("127.0.0.0/8"),  # loopback
    ipaddress.IPv4Network("169.254.0.0/16"),  # link-local / cloud metadata
    ipaddress.IPv4Network("100.64.0.0/10"),  # shared address space (RFC 6598)
    ipaddress.IPv4Network("192.0.0.0/24"),  # IETF protocol assignments
    ipaddress.IPv4Network("192.0.2.0/24"),  # TEST-NET-1 (documentation)
    ipaddress.IPv4Network("198.51.100.0/24"),  # TEST-NET-2 (documentation)
    ipaddress.IPv4Network("203.0.113.0/24"),  # TEST-NET-3 (documentation)
    ipaddress.IPv4Network("224.0.0.0/4"),  # multicast
    ipaddress.IPv4Network("240.0.0.0/4"),  # reserved
    ipaddress.IPv4Network("0.0.0.0/8"),  # "this" network
)

_BLOCKED_NETWORKS_V6: tuple[ipaddress.IPv6Network, ...] = (
    ipaddress.IPv6Network("::1/128"),  # loopback
    ipaddress.IPv6Network("fc00::/7"),  # unique local
    ipaddress.IPv6Network("fe80::/10"),  # link-local
    ipaddress.IPv6Network("ff00::/8"),  # multicast
    ipaddress.IPv6Network("::/128"),  # unspecified
)

_ALLOWED_SCHEMES = {"http", "https"}


def _check_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address, url: str) -> None:
    """Raise SecurityError if ``ip`` is private/reserved/internal.

    Three layers, in order:
    1. IPv4-mapped IPv6 (``::ffff:a.b.c.d``) is unwrapped to its IPv4 form so a
       literal like ``::ffff:127.0.0.1`` cannot smuggle loopback past the v6
       checks (the mapped address is in none of the blocked v6 networks).
    2. Explicit network lists (documentation/TEST-NET ranges that the stdlib
       property flags do not all cover).
    3. Property catch-all — blocks anything non-global that a future address
       class might add without us editing the explicit lists.
    """
    # 1. Unwrap IPv4-mapped IPv6 and re-dispatch as IPv4.
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        _check_ip(ip.ipv4_mapped, url)
        return

    # 2. Explicit network lists.
    if isinstance(ip, ipaddress.IPv4Address):
        for network in _BLOCKED_NETWORKS_V4:
            if ip in network:
                raise SecurityError(
                    f"URL '{url}' resolves to private/reserved IP {ip} "
                    f"({network}) — SSRF protection blocked this request."
                )
    else:
        for net6 in _BLOCKED_NETWORKS_V6:
            if ip in net6:
                raise SecurityError(
                    f"URL '{url}' resolves to private/reserved IPv6 {ip} "
                    f"({net6}) — SSRF protection blocked this request."
                )

    # 3. Property catch-all — defence in depth against ranges not listed above.
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    ):
        raise SecurityError(
            f"URL '{url}' resolves to non-global IP {ip} — SSRF protection blocked this request."
        )


def validate_url(url: str) -> None:
    """Validate that a URL is safe to fetch.

    Raises:
        SecurityError: if the URL is blocked for any reason.
    """
    url = url.strip()
    if not url:
        raise SecurityError("Empty URL is not allowed")

    try:
        parsed = urlparse(url)
    except Exception as exc:
        raise SecurityError(f"Malformed URL: {exc}") from exc

    # 1. Scheme check
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        raise SecurityError(
            f"URL scheme '{parsed.scheme}' is not allowed. Only http and https are permitted."
        )

    # 2. Host must be present
    host = parsed.hostname
    if not host:
        raise SecurityError("URL has no host component")

    # 3. Resolve hostname to IP and block private ranges
    try:
        addr_infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise SecurityError(f"Cannot resolve host '{host}': {exc}") from exc

    for addr_info in addr_infos:
        raw_ip = addr_info[4][0]
        try:
            ip = ipaddress.ip_address(raw_ip)
        except ValueError:
            continue
        _check_ip(ip, url)


def is_safe_url(url: str) -> bool:
    """Return True if the URL passes SSRF validation, False otherwise."""
    try:
        validate_url(url)
        return True
    except SecurityError:
        return False


async def ssrf_redirect_hook(response: Any) -> None:
    """httpx ``response`` event hook that re-runs SSRF validation on redirects.

    ``validate_url`` only guards the *initial* URL. With ``follow_redirects=True``
    an attacker-controlled (or compromised) source can answer with a ``3xx``
    ``Location: http://169.254.169.254/...`` (or ``http://127.0.0.1/...``) and
    reach an internal host that the first check never saw. Registering this hook
    validates the redirect target *before* httpx dispatches the next request:

        async with httpx.AsyncClient(
            follow_redirects=True,
            event_hooks={"response": [ssrf_redirect_hook]},
        ) as client:
            ...

    Relative ``Location`` values are resolved against the current URL. A blocked
    target raises ``SecurityError``, which aborts the redirect chain. The hook is
    header-only — it never reads the (still-unconsumed) response body.
    """
    if not getattr(response, "is_redirect", False):
        return
    location = response.headers.get("location")
    if not location:
        return
    # Resolve relative redirects against the URL that produced this response.
    try:
        target = str(response.url.join(location))
    except Exception:  # noqa: BLE001 — malformed Location is itself a rejection
        raise SecurityError(f"malformed redirect Location: {location!r}") from None
    validate_url(target)
