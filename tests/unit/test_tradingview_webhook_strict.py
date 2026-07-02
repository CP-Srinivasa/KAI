"""V8-f: hmac_strict_event_id auth mode + kill-switch + deprecation log.

Strict mode reuses shared-token credential check but binds the request to
the body via mandatory event_id + ts (with bounded clock skew). Kill-switch
hard-disables every token-based mode without env rotation.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routers import tradingview as tv_router
from app.api.routers.tradingview import _validate_strict_body_fields
from app.core.settings import AppSettings, TradingViewSettings

_TOKEN = "strict-test-token-32-bytes-padded"
_TOKEN_HEADER = "X-KAI-Token"


def _ts(offset_seconds: float = 0.0) -> str:
    return (datetime.now(UTC) + timedelta(seconds=offset_seconds)).isoformat()


# --- _validate_strict_body_fields (pure function) ------------------------


class TestStrictValidator:
    NOW = datetime(2026, 4, 25, 12, 0, 0, tzinfo=UTC)
    SKEW = 300

    def _check(self, payload: object) -> tuple[bool, str]:
        return _validate_strict_body_fields(payload, now=self.NOW, skew_seconds=self.SKEW)

    def test_happy_path(self) -> None:
        payload = {
            "ticker": "BTCUSDT",
            "action": "buy",
            "event_id": "tvalert-12345",
            "ts": self.NOW.isoformat(),
        }
        assert self._check(payload) == (True, "ok")

    def test_not_a_dict_rejected(self) -> None:
        assert self._check([1, 2, 3]) == (False, "not_a_dict")

    def test_missing_event_id(self) -> None:
        assert self._check({"ts": self.NOW.isoformat()}) == (False, "missing_event_id")

    def test_blank_event_id(self) -> None:
        assert self._check({"event_id": "   ", "ts": self.NOW.isoformat()}) == (
            False,
            "missing_event_id",
        )

    def test_event_id_too_short(self) -> None:
        assert self._check({"event_id": "abc", "ts": self.NOW.isoformat()}) == (
            False,
            "event_id_too_short",
        )

    def test_missing_ts(self) -> None:
        assert self._check({"event_id": "tvalert-12345"}) == (False, "missing_ts")

    def test_invalid_ts_format(self) -> None:
        assert self._check({"event_id": "tvalert-12345", "ts": "yesterday"}) == (
            False,
            "invalid_ts",
        )

    def test_naive_ts_rejected(self) -> None:
        # No tzinfo — strict mode demands timezone-aware ISO timestamps.
        assert self._check({"event_id": "tvalert-12345", "ts": "2026-04-25T12:00:00"}) == (
            False,
            "invalid_ts",
        )

    def test_skew_within_window_accepted(self) -> None:
        ts = (self.NOW - timedelta(seconds=299)).isoformat()
        assert self._check({"event_id": "tvalert-12345", "ts": ts}) == (True, "ok")

    def test_skew_outside_window_rejected(self) -> None:
        ts = (self.NOW - timedelta(seconds=301)).isoformat()
        assert self._check({"event_id": "tvalert-12345", "ts": ts}) == (
            False,
            "clock_skew",
        )

    def test_future_skew_outside_window_rejected(self) -> None:
        ts = (self.NOW + timedelta(seconds=600)).isoformat()
        assert self._check({"event_id": "tvalert-12345", "ts": ts}) == (
            False,
            "clock_skew",
        )


# --- end-to-end webhook with strict mode ---------------------------------


def _make_strict_client(
    audit_path: Path, *, kill_switch: bool = False, skew_seconds: int = 300
) -> Iterator[TestClient]:
    tv_router._reset_replay_cache_for_tests()
    tv_router.reset_audit_writer()
    tv_router._reset_deprecation_flag_for_tests()

    app = FastAPI()
    app.include_router(tv_router.router)
    settings = AppSettings()
    settings.tradingview = TradingViewSettings(
        webhook_enabled=True,
        webhook_secret="",
        webhook_audit_log=str(audit_path),
        webhook_auth_mode="hmac_strict_event_id",
        webhook_shared_token=_TOKEN,
        webhook_shared_token_disabled=kill_switch,
        webhook_strict_ts_skew_seconds=skew_seconds,
    )
    app.dependency_overrides[tv_router.get_settings] = lambda: settings
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()
    tv_router._reset_replay_cache_for_tests()


@pytest.fixture
def audit_path(tmp_path: Path) -> Path:
    return tmp_path / "audit.jsonl"


@pytest.fixture
def strict_client(audit_path: Path) -> Iterator[TestClient]:
    yield from _make_strict_client(audit_path)


@pytest.fixture
def kill_switch_client(audit_path: Path) -> Iterator[TestClient]:
    yield from _make_strict_client(audit_path, kill_switch=True)


def test_strict_accepts_valid_token_and_body(strict_client: TestClient, audit_path: Path) -> None:
    body = json.dumps(
        {
            "ticker": "BTCUSDT",
            "action": "buy",
            "event_id": "tvalert-12345",
            "ts": _ts(),
        }
    ).encode()
    resp = strict_client.post(
        "/tradingview/webhook",
        content=body,
        headers={_TOKEN_HEADER: _TOKEN, "Content-Type": "application/json"},
    )
    assert resp.status_code == 202
    entry = json.loads(audit_path.read_text(encoding="utf-8").splitlines()[0])
    assert entry["outcome"] == "accepted"
    assert entry["auth_method"] == "shared_token"
    assert entry["provenance"]["auth_method"] == "shared_token"


def test_strict_rejects_body_without_event_id(strict_client: TestClient, audit_path: Path) -> None:
    body = json.dumps({"ticker": "BTCUSDT", "action": "buy", "ts": _ts()}).encode()
    resp = strict_client.post(
        "/tradingview/webhook",
        content=body,
        headers={_TOKEN_HEADER: _TOKEN, "Content-Type": "application/json"},
    )
    assert resp.status_code == 401
    entry = json.loads(audit_path.read_text(encoding="utf-8").splitlines()[0])
    assert entry["outcome"] == "rejected"
    assert entry["reason"] == "strict_missing_event_id"
    # Brute-force counter incremented under the auth-bucket.
    assert entry["rate_limit_failures"] == 1


def test_strict_rejects_clock_skew(strict_client: TestClient, audit_path: Path) -> None:
    body = json.dumps(
        {
            "ticker": "BTCUSDT",
            "action": "buy",
            "event_id": "tvalert-12345",
            "ts": _ts(-3600),  # one hour stale
        }
    ).encode()
    resp = strict_client.post(
        "/tradingview/webhook",
        content=body,
        headers={_TOKEN_HEADER: _TOKEN, "Content-Type": "application/json"},
    )
    assert resp.status_code == 401
    entry = json.loads(audit_path.read_text(encoding="utf-8").splitlines()[0])
    assert entry["reason"] == "strict_clock_skew"


def test_strict_rejects_short_event_id(strict_client: TestClient, audit_path: Path) -> None:
    body = json.dumps({"ticker": "BTC", "action": "buy", "event_id": "abc", "ts": _ts()}).encode()
    resp = strict_client.post(
        "/tradingview/webhook",
        content=body,
        headers={_TOKEN_HEADER: _TOKEN, "Content-Type": "application/json"},
    )
    assert resp.status_code == 401
    entry = json.loads(audit_path.read_text(encoding="utf-8").splitlines()[0])
    assert entry["reason"] == "strict_event_id_too_short"


def test_strict_token_failure_rejected_before_body_check(
    strict_client: TestClient, audit_path: Path
) -> None:
    """Wrong token short-circuits at credential layer — strict body never read."""
    body = json.dumps(
        {"ticker": "BTC", "action": "buy", "event_id": "tvalert-12345", "ts": _ts()}
    ).encode()
    resp = strict_client.post(
        "/tradingview/webhook",
        content=body,
        headers={_TOKEN_HEADER: "wrong-token"},
    )
    assert resp.status_code == 401
    entry = json.loads(audit_path.read_text(encoding="utf-8").splitlines()[0])
    assert entry["reason"] == "invalid_shared_token"


# --- kill-switch ---------------------------------------------------------


def test_kill_switch_rejects_even_valid_token(
    kill_switch_client: TestClient, audit_path: Path
) -> None:
    body = json.dumps(
        {"ticker": "BTC", "action": "buy", "event_id": "tvalert-12345", "ts": _ts()}
    ).encode()
    resp = kill_switch_client.post(
        "/tradingview/webhook",
        content=body,
        headers={_TOKEN_HEADER: _TOKEN},
    )
    assert resp.status_code == 401
    entry = json.loads(audit_path.read_text(encoding="utf-8").splitlines()[0])
    assert entry["reason"] == "shared_token_disabled"


# --- deprecation log -----------------------------------------------------


def test_deprecation_log_fires_once_for_legacy_modes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Legacy shared_token / hmac_or_token emit one warning per process."""
    tv_router._reset_replay_cache_for_tests()
    tv_router.reset_audit_writer()
    tv_router._reset_deprecation_flag_for_tests()

    calls: list[tuple[str, dict[str, object]]] = []
    real_warning = tv_router._logger.warning

    def _capture(event: str, **kwargs: object) -> None:
        calls.append((event, kwargs))
        real_warning(event, **kwargs)

    monkeypatch.setattr(tv_router._logger, "warning", _capture)

    audit = tmp_path / "audit.jsonl"
    app = FastAPI()
    app.include_router(tv_router.router)
    settings = AppSettings()
    settings.tradingview = TradingViewSettings(
        webhook_enabled=True,
        webhook_secret="",
        webhook_audit_log=str(audit),
        webhook_auth_mode="shared_token",
        webhook_shared_token=_TOKEN,
    )
    app.dependency_overrides[tv_router.get_settings] = lambda: settings

    with TestClient(app) as client:
        for i in range(3):
            client.post(
                "/tradingview/webhook",
                content=json.dumps({"alert": f"x{i}"}).encode(),
                headers={_TOKEN_HEADER: _TOKEN},
            )

    deprecation_calls = [c for c in calls if c[0] == "tradingview_webhook_auth_mode_deprecated"]
    assert len(deprecation_calls) == 1
    assert deprecation_calls[0][1].get("mode") == "shared_token"
    app.dependency_overrides.clear()


def test_deprecation_log_silent_for_strict_mode(
    strict_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Strict mode is the migration target — no warning."""
    calls: list[str] = []
    real_warning = tv_router._logger.warning

    def _capture(event: str, **kwargs: object) -> None:
        calls.append(event)
        real_warning(event, **kwargs)

    monkeypatch.setattr(tv_router._logger, "warning", _capture)

    body = json.dumps(
        {"ticker": "BTC", "action": "buy", "event_id": "tvalert-12345", "ts": _ts()}
    ).encode()
    strict_client.post(
        "/tradingview/webhook",
        content=body,
        headers={_TOKEN_HEADER: _TOKEN},
    )
    assert "tradingview_webhook_auth_mode_deprecated" not in calls
