"""Shared HTTP helper for exploration probes.

Centralises the things every probe must do, so individual probes stay tiny:
  - SSRF validation via the production guard (app.security.ssrf) — allowed
    low-level shared dependency, NOT a runtime-module import.
  - honest User-Agent identifying the research crawler.
  - never raises: returns an ``HttpResponse`` with ``ok=False`` + ``error`` on any
    failure (timeout, transport error, SSRF rejection, non-2xx).
  - best-effort latency + byte accounting for the coverage report.

This helper does NOT bypass auth, paywalls, or CAPTCHAs — that is a hard line
even under DEC-SRC-EXPLORE-001.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.security.ssrf import validate_url

logger = logging.getLogger(__name__)


@dataclass
class HttpResponse:
    ok: bool
    status: int | None = None
    json: Any = None
    text: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    latency_ms: float | None = None
    bytes: int | None = None
    error: str | None = None

    @property
    def rate_limit_remaining(self) -> str | None:
        for key in (
            "x-ratelimit-remaining",
            "x-rate-limit-remaining",
            "ratelimit-remaining",
            "x-cg-pro-api-remaining",
        ):
            if key in self.headers:
                return self.headers[key]
        return None


async def fetch(
    url: str,
    *,
    method: str = "GET",
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    json_body: Any = None,
    timeout: int = 20,
    user_agent: str = "KAI-Exploration/0.1 (+research)",
    expect: str = "json",
) -> HttpResponse:
    """Perform one HTTP request defensively. Never raises.

    Args:
        expect: "json" parses the body as JSON; "text" returns raw text.
    """
    try:
        validate_url(url)
    except Exception as exc:  # noqa: BLE001 — SSRF rejection is an expected outcome
        return HttpResponse(ok=False, error=f"ssrf_rejected:{exc}")

    merged_headers = {"User-Agent": user_agent}
    if headers:
        merged_headers.update(headers)

    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.request(
                method.upper(),
                url,
                params=params,
                headers=merged_headers,
                json=json_body,
            )
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        body_bytes = len(response.content or b"")
        resp_headers = {k.lower(): v for k, v in response.headers.items()}

        if response.status_code >= 400:
            return HttpResponse(
                ok=False,
                status=response.status_code,
                headers=resp_headers,
                latency_ms=latency_ms,
                bytes=body_bytes,
                error=f"http_{response.status_code}",
                text=response.text[:2000] if expect == "text" else None,
            )

        parsed_json: Any = None
        text: str | None = None
        if expect == "json":
            try:
                parsed_json = response.json()
            except Exception:  # noqa: BLE001 — non-JSON body on a JSON endpoint
                text = response.text
        else:
            text = response.text

        return HttpResponse(
            ok=True,
            status=response.status_code,
            json=parsed_json,
            text=text,
            headers=resp_headers,
            latency_ms=latency_ms,
            bytes=body_bytes,
        )
    except httpx.TimeoutException:
        return HttpResponse(
            ok=False,
            error="timeout",
            latency_ms=round((time.monotonic() - start) * 1000, 1),
        )
    except Exception as exc:  # noqa: BLE001 — transport / DNS / TLS error
        return HttpResponse(
            ok=False,
            error=f"request_error:{type(exc).__name__}:{exc}",
            latency_ms=round((time.monotonic() - start) * 1000, 1),
        )
