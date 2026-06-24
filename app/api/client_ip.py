"""Trusted client-IP resolution behind the cloudflared / reverse-proxy.

``request.client.host`` is the TUNNEL, not the caller. Behind cloudflared the real
client IP is in ``CF-Connecting-IP`` (preferred), else the first hop of
``X-Forwarded-For``. Used for per-caller rate-limiting AND the demand fingerprint, so
both share ONE source of truth for "who is the requester" — without it, the per-key
mint-limiter cap and the demand fingerprint both collapse onto the tunnel IP.

NOTE: this trusts the proxy-set headers. It is only meaningful when the app is
reachable ONLY through the trusted proxy (the KAI deployment is — cloudflared in
front). It is a demand/abuse heuristic, never an authentication signal.
"""

from __future__ import annotations

from fastapi import Request


def resolve_client_ip(request: Request) -> str:
    """Best-effort real client IP: ``CF-Connecting-IP`` → first ``X-Forwarded-For``
    hop → socket peer → ``"unknown"``."""
    cf = request.headers.get("CF-Connecting-IP")
    if cf and cf.strip():
        return cf.strip()
    xff = request.headers.get("X-Forwarded-For")
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else "unknown"
