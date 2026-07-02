"""U2 — trusted client-IP resolution behind the cloudflared/reverse-proxy.

Behind the tunnel ``request.client.host`` is the proxy, not the caller. The real IP
must come from ``CF-Connecting-IP`` (preferred) or the first hop of
``X-Forwarded-For`` — otherwise per-caller rate-limiting and the demand fingerprint
both collapse onto a single value.
"""

from __future__ import annotations

from starlette.requests import Request

from app.api.client_ip import resolve_client_ip


def _req(headers: dict[str, str] | None = None, client_host: str = "10.0.0.1") -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "headers": raw, "client": (client_host, 1234)})


def test_cf_connecting_ip_wins() -> None:
    r = _req({"CF-Connecting-IP": "203.0.113.5", "X-Forwarded-For": "9.9.9.9"})
    assert resolve_client_ip(r) == "203.0.113.5"


def test_first_xff_hop_when_no_cf() -> None:
    r = _req({"X-Forwarded-For": "203.0.113.6, 70.0.0.1, 10.0.0.2"})
    assert resolve_client_ip(r) == "203.0.113.6"


def test_falls_back_to_socket_peer() -> None:
    r = _req(client_host="172.16.0.9")
    assert resolve_client_ip(r) == "172.16.0.9"
