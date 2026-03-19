"""SSRF (Server-Side Request Forgery) protection.

Every outbound HTTP request from ingestion modules MUST call validate_url()
before opening a connection.

Blocked:
- Non-http/https schemes (file://, ftp://, gopher://, etc.)
- Private IPv4 ranges (RFC 1918): 10/8, 172.16/12, 192.168/16
- Loopback: 127.0.0.0/8, ::1
- Link-local / cloud metadata: 169.254.0.0/16 (AWS, GCP, Azure metadata)
- Multicast: 224.0.0.0/4
- Missing or empty host

Usage:
    from app.security.ssrf import validate_url
    validate_url(url)          # raises SSRFError on violation
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from app.core.errors import SecurityError

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

        if isinstance(ip, ipaddress.IPv4Address):
            for network in _BLOCKED_NETWORKS_V4:
                if ip in network:
                    raise SecurityError(
                        f"URL '{url}' resolves to private/reserved IP {ip} "
                        f"({network}) — SSRF protection blocked this request."
                    )
        elif isinstance(ip, ipaddress.IPv6Address):
            for network in _BLOCKED_NETWORKS_V6:
                if ip in network:
                    raise SecurityError(
                        f"URL '{url}' resolves to private/reserved IPv6 {ip} "
                        f"({network}) — SSRF protection blocked this request."
                    )


def is_safe_url(url: str) -> bool:
    """Return True if the URL passes SSRF validation, False otherwise."""
    try:
        validate_url(url)
        return True
    except SecurityError:
        return False
