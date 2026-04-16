"""Tests for the TV-2.1 shared-token / hmac_or_token webhook auth modes.

Strategy: parameterize the AppSettings dependency with the relevant auth mode,
then verify request acceptance/rejection paths. The default-HMAC tests live in
test_tradingview_webhook.py — this file only covers the new modes.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routers import tradingview as tv_router
from app.core.settings import AppSettings, TradingViewSettings

_HMAC_SECRET = "hmac-test-secret-32-bytes-padded!"
_SHARED_TOKEN = "shared-test-token-32-bytes-padded"
_SIGNATURE_HEADER = "X-KAI-Signature"
_TOKEN_HEADER = "X-KAI-Token"


def _sign(body: bytes, secret: str = _HMAC_SECRET) -> str:
    return f"sha256={hmac.new(secret.encode('utf-8'), body, hashlib.sha256).hexdigest()}"


def _make_client(
    audit_path: Path,
    *,
    auth_mode: str,
    secret: str = _HMAC_SECRET,
    shared_token: str = _SHARED_TOKEN,
    enabled: bool = True,
) -> Iterator[TestClient]:
    tv_router._reset_replay_cache_for_tests()
    tv_router.reset_audit_writer()

    app = FastAPI()
    app.include_router(tv_router.router)
    settings = AppSettings()
    settings.tradingview = TradingViewSettings(
        webhook_enabled=enabled,
        webhook_secret=secret,
        webhook_audit_log=str(audit_path),
        webhook_auth_mode=auth_mode,
        webhook_shared_token=shared_token,
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
def shared_token_client(audit_path: Path) -> Iterator[TestClient]:
    yield from _make_client(audit_path, auth_mode="shared_token", secret="")


@pytest.fixture
def hmac_or_token_client(audit_path: Path) -> Iterator[TestClient]:
    yield from _make_client(audit_path, auth_mode="hmac_or_token")


# --- shared_token mode ----------------------------------------------------


def test_shared_token_accepts_correct_token(
    shared_token_client: TestClient, audit_path: Path
) -> None:
    body = b'{"alert":"trigger"}'
    resp = shared_token_client.post(
        "/tradingview/webhook",
        content=body,
        headers={_TOKEN_HEADER: _SHARED_TOKEN, "Content-Type": "application/json"},
    )
    assert resp.status_code == 202
    body_json = resp.json()
    assert body_json["status"] == "accepted"
    # Audit log carries the auth_method.
    audit_lines = audit_path.read_text(encoding="utf-8").splitlines()
    assert len(audit_lines) == 1
    entry = json.loads(audit_lines[0])
    assert entry["outcome"] == "accepted"
    assert entry["auth_mode"] == "shared_token"
    assert entry["auth_method"] == "shared_token"
    assert entry["provenance"]["auth_method"] == "shared_token"


def test_shared_token_rejects_wrong_token(shared_token_client: TestClient) -> None:
    resp = shared_token_client.post(
        "/tradingview/webhook",
        content=b'{}',
        headers={_TOKEN_HEADER: "wrong-token"},
    )
    assert resp.status_code == 401


def test_shared_token_rejects_hmac_signature_in_token_only_mode(
    shared_token_client: TestClient,
) -> None:
    body = b'{"alert":"trigger"}'
    resp = shared_token_client.post(
        "/tradingview/webhook",
        content=body,
        headers={_SIGNATURE_HEADER: _sign(body)},
    )
    # In pure shared_token mode HMAC alone is NOT accepted.
    assert resp.status_code == 401


def test_shared_token_missing_header_rejected(shared_token_client: TestClient) -> None:
    resp = shared_token_client.post("/tradingview/webhook", content=b'{}')
    assert resp.status_code == 401


def test_shared_token_unconfigured_returns_404(audit_path: Path) -> None:
    # No shared token set + auth_mode=shared_token would fail validation,
    # so we skip the validator by constructing settings directly.
    tv_router._reset_replay_cache_for_tests()
    tv_router.reset_audit_writer()
    app = FastAPI()
    app.include_router(tv_router.router)

    settings = AppSettings()
    object.__setattr__(
        settings,
        "tradingview",
        TradingViewSettings.model_construct(
            webhook_enabled=True,
            webhook_secret="",
            webhook_audit_log=str(audit_path),
            webhook_replay_cache_size=256,
            webhook_replay_window_seconds=300.0,
            webhook_auth_mode="shared_token",
            webhook_shared_token="",  # empty → endpoint must 404
        ),
    )
    app.dependency_overrides[tv_router.get_settings] = lambda: settings
    with TestClient(app) as client:
        resp = client.post(
            "/tradingview/webhook",
            content=b'{}',
            headers={_TOKEN_HEADER: "anything"},
        )
    assert resp.status_code == 404


# --- hmac_or_token mode ---------------------------------------------------


def test_hmac_or_token_accepts_hmac(
    hmac_or_token_client: TestClient, audit_path: Path
) -> None:
    body = b'{"alert":"trigger"}'
    resp = hmac_or_token_client.post(
        "/tradingview/webhook",
        content=body,
        headers={_SIGNATURE_HEADER: _sign(body)},
    )
    assert resp.status_code == 202
    entry = json.loads(audit_path.read_text(encoding="utf-8").splitlines()[0])
    assert entry["auth_method"] == "hmac"


def test_hmac_or_token_accepts_shared_token(
    hmac_or_token_client: TestClient, audit_path: Path
) -> None:
    body = b'{"alert":"trigger-via-token"}'
    resp = hmac_or_token_client.post(
        "/tradingview/webhook",
        content=body,
        headers={_TOKEN_HEADER: _SHARED_TOKEN},
    )
    assert resp.status_code == 202
    entry = json.loads(audit_path.read_text(encoding="utf-8").splitlines()[0])
    assert entry["auth_method"] == "shared_token"


def test_hmac_or_token_rejects_when_both_wrong(
    hmac_or_token_client: TestClient,
) -> None:
    body = b'{"alert":"trigger"}'
    resp = hmac_or_token_client.post(
        "/tradingview/webhook",
        content=body,
        headers={
            _SIGNATURE_HEADER: "sha256=deadbeef",
            _TOKEN_HEADER: "wrong-token",
        },
    )
    assert resp.status_code == 401


def test_hmac_or_token_prefers_hmac_when_both_present(
    hmac_or_token_client: TestClient, audit_path: Path
) -> None:
    body = b'{"alert":"both"}'
    resp = hmac_or_token_client.post(
        "/tradingview/webhook",
        content=body,
        headers={
            _SIGNATURE_HEADER: _sign(body),
            _TOKEN_HEADER: _SHARED_TOKEN,
        },
    )
    assert resp.status_code == 202
    entry = json.loads(audit_path.read_text(encoding="utf-8").splitlines()[0])
    assert entry["auth_method"] == "hmac"


# --- settings validation --------------------------------------------------


def test_invalid_auth_mode_raises() -> None:
    with pytest.raises(Exception, match="WEBHOOK_AUTH_MODE"):
        TradingViewSettings(
            webhook_enabled=True,
            webhook_secret="x",
            webhook_auth_mode="bogus",
        )


def test_shared_token_mode_requires_shared_token() -> None:
    with pytest.raises(Exception, match="WEBHOOK_SHARED_TOKEN"):
        TradingViewSettings(
            webhook_enabled=True,
            webhook_auth_mode="shared_token",
            webhook_shared_token="",
        )


def test_hmac_or_token_mode_requires_shared_token() -> None:
    with pytest.raises(Exception, match="WEBHOOK_SHARED_TOKEN"):
        TradingViewSettings(
            webhook_enabled=True,
            webhook_secret="x",
            webhook_auth_mode="hmac_or_token",
            webhook_shared_token="",
        )
