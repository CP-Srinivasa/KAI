"""D-193 / NEO-F-META-20260424-023 — webhook brute-force guard.

Independent of the API-Key brute-force guard in app.security.auth. We verify:
  (a) fresh IP is not limited,
  (b) N consecutive auth-failures lock the IP with HTTP 429 + Retry-After,
  (c) a successful auth resets the IP's failure counter,
  (d) threshold=0 disables the guard (operator opt-out),
  (e) Cf-Connecting-IP + X-Forwarded-For routed correctly,
  (f) FailureTracker unit tests (window-prune, multi-key isolation).
"""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routers import tradingview as tv_router
from app.core.settings import (
    AppSettings,
    DBSettings,
    TradingViewSettings,
    get_settings,
)
from app.security.rate_limit import FailureTracker, client_ip

# ---------------------------------------------------------------------------
# FailureTracker unit tests (covers the extracted helper)
# ---------------------------------------------------------------------------


def test_fresh_key_is_not_limited() -> None:
    tracker = FailureTracker(window_seconds=60.0, threshold=3)
    locked, retry = tracker.is_limited("ip-a")
    assert locked is False
    assert retry == 0


def test_threshold_reached_locks_with_positive_retry_after() -> None:
    tracker = FailureTracker(window_seconds=60.0, threshold=3)
    for _ in range(3):
        tracker.record_failure("ip-a", now=100.0)
    locked, retry = tracker.is_limited("ip-a", now=100.0)
    assert locked is True
    assert retry >= 1


def test_reset_clears_failures() -> None:
    tracker = FailureTracker(window_seconds=60.0, threshold=2)
    tracker.record_failure("ip-a", now=10.0)
    tracker.record_failure("ip-a", now=10.0)
    assert tracker.is_limited("ip-a", now=10.0)[0] is True
    tracker.reset("ip-a")
    assert tracker.is_limited("ip-a", now=10.0)[0] is False


def test_window_expires_old_failures() -> None:
    tracker = FailureTracker(window_seconds=60.0, threshold=2)
    tracker.record_failure("ip-a", now=0.0)
    tracker.record_failure("ip-a", now=0.0)
    # Now look 120 s later — the failures are outside the window.
    locked, _ = tracker.is_limited("ip-a", now=120.0)
    assert locked is False


def test_multi_key_isolation() -> None:
    tracker = FailureTracker(window_seconds=60.0, threshold=2)
    tracker.record_failure("ip-a", now=0.0)
    tracker.record_failure("ip-a", now=0.0)
    # ip-a should be locked, ip-b unaffected.
    assert tracker.is_limited("ip-a", now=0.0)[0] is True
    assert tracker.is_limited("ip-b", now=0.0)[0] is False


def test_threshold_zero_disables_tracker() -> None:
    tracker = FailureTracker(window_seconds=60.0, threshold=0)
    for _ in range(100):
        tracker.record_failure("ip-a")
    assert tracker.is_limited("ip-a")[0] is False


# ---------------------------------------------------------------------------
# Webhook-level integration
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_caches_and_limiter() -> Any:
    tv_router._reset_replay_cache_for_tests()
    tv_router._reset_rate_limiter_for_tests()
    yield
    tv_router._reset_replay_cache_for_tests()
    tv_router._reset_rate_limiter_for_tests()


def _settings(
    *,
    threshold: int = 3,
    window: float = 300.0,
    secret: str = "topsecret",
) -> AppSettings:
    return AppSettings(
        env="test",
        api_key="operator-key",
        db=DBSettings(url="sqlite+aiosqlite:///:memory:"),
        tradingview=TradingViewSettings(
            webhook_enabled=True,
            webhook_secret=secret,
            webhook_auth_mode="hmac",
            webhook_rate_limit_threshold=threshold,
            webhook_rate_limit_window_seconds=window,
        ),
    )


def _app(settings: AppSettings, audit_path: Path) -> FastAPI:
    app = FastAPI()
    app.dependency_overrides[get_settings] = lambda: settings
    app.include_router(tv_router.router)
    # Silence disk writes during the test run.
    tv_router.set_audit_writer(lambda _path, _entry: None)
    # The audit_log path is still read from settings; point it at tmp_path
    # so nothing surprises us if the writer changes back to disk.
    settings.tradingview.webhook_audit_log = str(audit_path)
    return app


def _signed_body(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def test_webhook_429_after_threshold_from_same_ip(tmp_path: Path) -> None:
    settings = _settings(threshold=3)
    app = _app(settings, tmp_path / "audit.jsonl")
    try:
        with TestClient(app) as client:
            # Three bad credentials → 401 each; fourth → 429.
            body = json.dumps({"symbol": "BTCUSD", "side": "long"}).encode()
            bad_sig = "sha256=deadbeef"
            for _ in range(3):
                res = client.post(
                    "/tradingview/webhook",
                    content=body,
                    headers={
                        "X-KAI-Signature": bad_sig,
                        "Cf-Connecting-IP": "203.0.113.5",
                    },
                )
                assert res.status_code == 401

            res = client.post(
                "/tradingview/webhook",
                content=body,
                headers={
                    "X-KAI-Signature": bad_sig,
                    "Cf-Connecting-IP": "203.0.113.5",
                },
            )
            assert res.status_code == 429
            assert int(res.headers.get("Retry-After", "0")) >= 1
    finally:
        tv_router.reset_audit_writer()


def test_webhook_success_resets_failure_counter(tmp_path: Path) -> None:
    settings = _settings(threshold=2)
    app = _app(settings, tmp_path / "audit.jsonl")
    try:
        with TestClient(app) as client:
            body = json.dumps({"symbol": "ETHUSD", "side": "long"}).encode()
            # One failure counts, then a successful auth clears the counter.
            client.post(
                "/tradingview/webhook",
                content=body,
                headers={
                    "X-KAI-Signature": "sha256=deadbeef",
                    "Cf-Connecting-IP": "203.0.113.7",
                },
            )
            ok_res = client.post(
                "/tradingview/webhook",
                content=body,
                headers={
                    "X-KAI-Signature": _signed_body(settings.tradingview.webhook_secret, body),
                    "Cf-Connecting-IP": "203.0.113.7",
                },
            )
            assert ok_res.status_code == 202

            # After a successful auth, two more failures must NOT lock —
            # counter was reset. First one 401, second one 401 (still under
            # threshold).
            for _ in range(2):
                res = client.post(
                    "/tradingview/webhook",
                    content=b'{"different":"body"}',
                    headers={
                        "X-KAI-Signature": "sha256=deadbeef",
                        "Cf-Connecting-IP": "203.0.113.7",
                    },
                )
                assert res.status_code == 401
    finally:
        tv_router.reset_audit_writer()


def test_webhook_threshold_zero_disables_guard(tmp_path: Path) -> None:
    settings = _settings(threshold=0)
    app = _app(settings, tmp_path / "audit.jsonl")
    try:
        with TestClient(app) as client:
            body = b'{"symbol":"BTCUSD","side":"long"}'
            # 50 failures from the same IP — should all be 401, never 429.
            for _ in range(50):
                res = client.post(
                    "/tradingview/webhook",
                    content=body,
                    headers={
                        "X-KAI-Signature": "sha256=deadbeef",
                        "Cf-Connecting-IP": "203.0.113.9",
                    },
                )
                assert res.status_code == 401
    finally:
        tv_router.reset_audit_writer()


def test_webhook_ips_isolated_from_each_other(tmp_path: Path) -> None:
    settings = _settings(threshold=2)
    app = _app(settings, tmp_path / "audit.jsonl")
    try:
        with TestClient(app) as client:
            body = b'{"symbol":"BTCUSD","side":"long"}'
            # Lock ip-X with 2 failures.
            for _ in range(2):
                client.post(
                    "/tradingview/webhook",
                    content=body,
                    headers={
                        "X-KAI-Signature": "sha256=deadbeef",
                        "Cf-Connecting-IP": "203.0.113.1",
                    },
                )
            # Third call from ip-X must be 429.
            res_locked = client.post(
                "/tradingview/webhook",
                content=body,
                headers={
                    "X-KAI-Signature": "sha256=deadbeef",
                    "Cf-Connecting-IP": "203.0.113.1",
                },
            )
            assert res_locked.status_code == 429

            # ip-Y from the same test must NOT be locked.
            res_other = client.post(
                "/tradingview/webhook",
                content=body,
                headers={
                    "X-KAI-Signature": "sha256=deadbeef",
                    "Cf-Connecting-IP": "203.0.113.2",
                },
            )
            assert res_other.status_code == 401  # bad creds, but not rate-limited
    finally:
        tv_router.reset_audit_writer()


def test_client_ip_prefers_cf_connecting_ip() -> None:
    """The public ``client_ip`` helper must return Cf-Connecting-IP first."""
    from starlette.requests import Request as StarletteRequest

    scope: dict[str, Any] = {
        "type": "http",
        "headers": [
            (b"cf-connecting-ip", b"203.0.113.5"),
            (b"x-forwarded-for", b"10.0.0.1, 203.0.113.5"),
        ],
        "client": ("192.168.1.1", 12345),
    }
    request = StarletteRequest(scope)
    assert client_ip(request) == "203.0.113.5"
