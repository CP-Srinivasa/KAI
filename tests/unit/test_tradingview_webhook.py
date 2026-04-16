"""Tests for the TradingView webhook ingestor (TV-1, audit-only)."""

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

_TEST_SECRET = "unit-test-secret-value-32-bytes!!"
_SIGNATURE_HEADER = "X-KAI-Signature"


def _sign(body: bytes, secret: str = _TEST_SECRET) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _settings_factory(
    *,
    enabled: bool,
    secret: str,
    audit_path: Path,
) -> AppSettings:
    settings = AppSettings()
    settings.tradingview = TradingViewSettings(
        webhook_enabled=enabled,
        webhook_secret=secret,
        webhook_audit_log=str(audit_path),
    )
    return settings


@pytest.fixture
def audit_path(tmp_path: Path) -> Path:
    return tmp_path / "tradingview_webhook_audit.jsonl"


@pytest.fixture
def enabled_client(audit_path: Path) -> Iterator[TestClient]:
    tv_router._reset_replay_cache_for_tests()
    tv_router.reset_audit_writer()

    app = FastAPI()
    app.include_router(tv_router.router)
    settings = _settings_factory(enabled=True, secret=_TEST_SECRET, audit_path=audit_path)
    app.dependency_overrides[tv_router.get_settings] = lambda: settings

    with TestClient(app) as client:
        yield client

    app.dependency_overrides.clear()
    tv_router._reset_replay_cache_for_tests()
    tv_router.reset_audit_writer()


@pytest.fixture
def disabled_client(audit_path: Path) -> Iterator[TestClient]:
    tv_router._reset_replay_cache_for_tests()
    tv_router.reset_audit_writer()

    app = FastAPI()
    app.include_router(tv_router.router)
    settings = _settings_factory(enabled=False, secret=_TEST_SECRET, audit_path=audit_path)
    app.dependency_overrides[tv_router.get_settings] = lambda: settings

    with TestClient(app) as client:
        yield client

    app.dependency_overrides.clear()


@pytest.fixture
def missing_secret_client(audit_path: Path) -> Iterator[TestClient]:
    tv_router._reset_replay_cache_for_tests()
    tv_router.reset_audit_writer()

    app = FastAPI()
    app.include_router(tv_router.router)
    settings = _settings_factory(enabled=True, secret="", audit_path=audit_path)
    app.dependency_overrides[tv_router.get_settings] = lambda: settings

    with TestClient(app) as client:
        yield client

    app.dependency_overrides.clear()


def _read_audit(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_returns_404_when_flag_disabled(disabled_client: TestClient) -> None:
    body = b'{"symbol":"BTCUSDT"}'
    resp = disabled_client.post(
        "/tradingview/webhook",
        content=body,
        headers={_SIGNATURE_HEADER: _sign(body)},
    )
    assert resp.status_code == 404


def test_returns_404_when_secret_missing(missing_secret_client: TestClient) -> None:
    body = b'{"symbol":"BTCUSDT"}'
    resp = missing_secret_client.post(
        "/tradingview/webhook",
        content=body,
        headers={_SIGNATURE_HEADER: _sign(body)},
    )
    assert resp.status_code == 404


def test_returns_401_when_signature_missing(
    enabled_client: TestClient, audit_path: Path
) -> None:
    body = b'{"symbol":"BTCUSDT"}'
    resp = enabled_client.post("/tradingview/webhook", content=body)
    assert resp.status_code == 401
    records = _read_audit(audit_path)
    assert len(records) == 1
    assert records[0]["outcome"] == "rejected"
    assert records[0]["reason"] == "invalid_signature"


def test_returns_401_when_signature_invalid(
    enabled_client: TestClient, audit_path: Path
) -> None:
    body = b'{"symbol":"BTCUSDT"}'
    resp = enabled_client.post(
        "/tradingview/webhook",
        content=body,
        headers={_SIGNATURE_HEADER: "sha256=deadbeef"},
    )
    assert resp.status_code == 401
    records = _read_audit(audit_path)
    assert records[0]["reason"] == "invalid_signature"


def test_returns_401_when_signature_wrong_secret(
    enabled_client: TestClient, audit_path: Path
) -> None:
    body = b'{"symbol":"BTCUSDT"}'
    resp = enabled_client.post(
        "/tradingview/webhook",
        content=body,
        headers={_SIGNATURE_HEADER: _sign(body, "wrong-secret")},
    )
    assert resp.status_code == 401


def test_returns_401_when_prefix_missing(enabled_client: TestClient) -> None:
    body = b'{"symbol":"BTCUSDT"}'
    digest = hmac.new(_TEST_SECRET.encode(), body, hashlib.sha256).hexdigest()
    # missing "sha256=" prefix
    resp = enabled_client.post(
        "/tradingview/webhook",
        content=body,
        headers={_SIGNATURE_HEADER: digest},
    )
    assert resp.status_code == 401


def test_accepts_valid_signed_payload(
    enabled_client: TestClient, audit_path: Path
) -> None:
    body = b'{"symbol":"BTCUSDT","action":"buy","price":65000}'
    resp = enabled_client.post(
        "/tradingview/webhook",
        content=body,
        headers={_SIGNATURE_HEADER: _sign(body)},
    )
    assert resp.status_code == 202
    data = resp.json()
    assert data["status"] == "accepted"
    assert data["request_id"].startswith("tvwh_")

    records = _read_audit(audit_path)
    assert len(records) == 1
    entry = records[0]
    assert entry["outcome"] == "accepted"
    assert entry["payload"]["symbol"] == "BTCUSDT"
    assert entry["payload"]["action"] == "buy"
    # Provenance tagging per D-125 Bedingung 3
    assert entry["provenance"]["source"] == "tradingview_webhook"
    assert entry["provenance"]["version"] == "tv-1"
    # TV-1: no pipeline routing
    assert entry["provenance"]["signal_path_id"] is None


def test_duplicate_payload_rejected_as_replay(
    enabled_client: TestClient, audit_path: Path
) -> None:
    body = b'{"symbol":"BTCUSDT","action":"buy"}'
    headers = {_SIGNATURE_HEADER: _sign(body)}

    first = enabled_client.post("/tradingview/webhook", content=body, headers=headers)
    assert first.status_code == 202

    second = enabled_client.post("/tradingview/webhook", content=body, headers=headers)
    assert second.status_code == 409

    records = _read_audit(audit_path)
    assert len(records) == 2
    assert records[0]["outcome"] == "accepted"
    assert records[1]["outcome"] == "rejected"
    assert records[1]["reason"] == "replay"


def test_different_payloads_both_accepted(enabled_client: TestClient) -> None:
    body_a = b'{"symbol":"BTCUSDT"}'
    body_b = b'{"symbol":"ETHUSDT"}'
    r1 = enabled_client.post(
        "/tradingview/webhook",
        content=body_a,
        headers={_SIGNATURE_HEADER: _sign(body_a)},
    )
    r2 = enabled_client.post(
        "/tradingview/webhook",
        content=body_b,
        headers={_SIGNATURE_HEADER: _sign(body_b)},
    )
    assert r1.status_code == 202
    assert r2.status_code == 202


def test_malformed_json_rejected_after_signature_passes(
    enabled_client: TestClient, audit_path: Path
) -> None:
    body = b"{not json"
    resp = enabled_client.post(
        "/tradingview/webhook",
        content=body,
        headers={_SIGNATURE_HEADER: _sign(body)},
    )
    assert resp.status_code == 400
    records = _read_audit(audit_path)
    assert records[-1]["outcome"] == "rejected"
    assert records[-1]["reason"] == "malformed_json"


def test_replay_cache_isolates_between_test_fixtures(audit_path: Path) -> None:
    """Ensures the singleton is really reset between fixture setups."""
    tv_router._reset_replay_cache_for_tests()
    cache_a = tv_router._get_replay_cache(
        TradingViewSettings(
            webhook_enabled=True,
            webhook_secret="x",
            webhook_audit_log=str(audit_path),
        )
    )
    assert cache_a.check_and_record("hash1") is True
    tv_router._reset_replay_cache_for_tests()
    cache_b = tv_router._get_replay_cache(
        TradingViewSettings(
            webhook_enabled=True,
            webhook_secret="x",
            webhook_audit_log=str(audit_path),
        )
    )
    # fresh cache — "hash1" is new again
    assert cache_b.check_and_record("hash1") is True


def test_audit_log_records_client_ip_and_size(
    enabled_client: TestClient, audit_path: Path
) -> None:
    body = b'{"symbol":"BTCUSDT"}'
    enabled_client.post(
        "/tradingview/webhook",
        content=body,
        headers={_SIGNATURE_HEADER: _sign(body)},
    )
    entry = _read_audit(audit_path)[0]
    assert entry["body_bytes"] == len(body)
    assert "source_ip" in entry
    assert "received_at" in entry
    assert entry["request_id"].startswith("tvwh_")
