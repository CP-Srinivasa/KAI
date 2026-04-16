"""Tests for TV-3 webhook -> pending-signal routing hook.

Strategy: parameterize AppSettings with routing enabled/disabled and verify:
- flag off  -> audit entry only (TV-1/TV-2 behavior preserved)
- flag on + good payload -> event appended to pending-signals JSONL
- flag on + bad payload   -> audit still accepted, routing marks normalize_failed
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

_HMAC_SECRET = "routing-test-secret-32-bytes-pad!"
_SIGNATURE_HEADER = "X-KAI-Signature"


def _sign(body: bytes, secret: str = _HMAC_SECRET) -> str:
    return f"sha256={hmac.new(secret.encode('utf-8'), body, hashlib.sha256).hexdigest()}"


@pytest.fixture
def audit_path(tmp_path: Path) -> Path:
    return tmp_path / "audit.jsonl"


@pytest.fixture
def pending_path(tmp_path: Path) -> Path:
    return tmp_path / "pending.jsonl"


def _make_client(
    audit_path: Path,
    pending_path: Path,
    *,
    routing_enabled: bool,
) -> Iterator[TestClient]:
    tv_router._reset_replay_cache_for_tests()
    tv_router.reset_audit_writer()
    tv_router.reset_pending_signal_writer()

    app = FastAPI()
    app.include_router(tv_router.router)
    settings = AppSettings()
    settings.tradingview = TradingViewSettings(
        webhook_enabled=True,
        webhook_secret=_HMAC_SECRET,
        webhook_audit_log=str(audit_path),
        webhook_signal_routing_enabled=routing_enabled,
        webhook_pending_signals_log=str(pending_path),
        # Pin auth_mode so an ambient .env cannot flip this HMAC fixture.
        webhook_auth_mode="hmac",
        webhook_shared_token="",
    )
    app.dependency_overrides[tv_router.get_settings] = lambda: settings
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()
    tv_router._reset_replay_cache_for_tests()
    tv_router.reset_pending_signal_writer()


@pytest.fixture
def routing_off_client(audit_path: Path, pending_path: Path) -> Iterator[TestClient]:
    yield from _make_client(audit_path, pending_path, routing_enabled=False)


@pytest.fixture
def routing_on_client(audit_path: Path, pending_path: Path) -> Iterator[TestClient]:
    yield from _make_client(audit_path, pending_path, routing_enabled=True)


# --- routing flag OFF (default / TV-1 parity) ---------------------------


def test_routing_off_no_pending_file_created(
    routing_off_client: TestClient, audit_path: Path, pending_path: Path
) -> None:
    body = json.dumps({"ticker": "BTCUSDT", "action": "buy", "price": 50000}).encode()
    resp = routing_off_client.post(
        "/tradingview/webhook",
        content=body,
        headers={_SIGNATURE_HEADER: _sign(body)},
    )
    assert resp.status_code == 202
    assert not pending_path.exists()

    audit_entry = json.loads(audit_path.read_text(encoding="utf-8").splitlines()[0])
    assert audit_entry["outcome"] == "accepted"
    # Version stays tv-1 while routing is off (audit-only semantics).
    assert audit_entry["provenance"]["version"] == "tv-1"
    assert audit_entry["provenance"]["signal_path_id"] is None
    assert audit_entry["routing"] == {"enabled": False, "status": "disabled"}
    # Response carries no event_id when routing is off.
    data = resp.json()
    assert "event_id" not in data
    assert "signal_path_id" not in data


# --- routing flag ON, happy path ----------------------------------------


def test_routing_on_emits_pending_event(
    routing_on_client: TestClient, audit_path: Path, pending_path: Path
) -> None:
    body = json.dumps(
        {"ticker": "ETHUSDT", "action": "sell", "price": "1900.5", "note": "rsi flip"}
    ).encode()
    resp = routing_on_client.post(
        "/tradingview/webhook",
        content=body,
        headers={_SIGNATURE_HEADER: _sign(body)},
    )
    assert resp.status_code == 202
    data = resp.json()
    assert data["event_id"].startswith("tvsig_")
    assert data["signal_path_id"].startswith("tvpath_")

    # Pending-signals JSONL has exactly one row, matching the response ids.
    pending_rows = pending_path.read_text(encoding="utf-8").splitlines()
    assert len(pending_rows) == 1
    event = json.loads(pending_rows[0])
    assert event["event_id"] == data["event_id"]
    assert event["ticker"] == "ETHUSDT"
    assert event["action"] == "sell"
    assert event["price"] == pytest.approx(1900.5)
    assert event["note"] == "rsi flip"
    assert event["source_request_id"]
    assert event["provenance"]["source"] == "tradingview_webhook"
    assert event["provenance"]["version"] == "tv-3"
    assert event["provenance"]["signal_path_id"] == data["signal_path_id"]

    # Audit entry reflects the routing outcome + promoted provenance version.
    audit_entry = json.loads(audit_path.read_text(encoding="utf-8").splitlines()[0])
    assert audit_entry["provenance"]["version"] == "tv-3"
    assert audit_entry["provenance"]["signal_path_id"] == data["signal_path_id"]
    assert audit_entry["routing"]["status"] == "emitted"
    assert audit_entry["routing"]["event_id"] == data["event_id"]


# --- routing flag ON, bad payload (routing fails, audit still accepted) --


def test_routing_on_normalize_failure_does_not_break_audit(
    routing_on_client: TestClient, audit_path: Path, pending_path: Path
) -> None:
    # Unsupported action — parses fine but normalizer rejects it.
    body = json.dumps({"ticker": "BTCUSDT", "action": "rebalance"}).encode()
    resp = routing_on_client.post(
        "/tradingview/webhook",
        content=body,
        headers={_SIGNATURE_HEADER: _sign(body)},
    )
    # Webhook still accepted — routing failure is audit-visible, not a 4xx.
    assert resp.status_code == 202
    data = resp.json()
    assert "event_id" not in data

    assert not pending_path.exists()

    audit_entry = json.loads(audit_path.read_text(encoding="utf-8").splitlines()[0])
    assert audit_entry["outcome"] == "accepted"
    assert audit_entry["routing"]["status"] == "normalize_failed"
    assert "rebalance" in audit_entry["routing"]["reason"]
    # Version falls back to tv-1 since no pipeline event was produced.
    assert audit_entry["provenance"]["version"] == "tv-1"
    assert audit_entry["provenance"]["signal_path_id"] is None


# --- routing flag ON, emit failure (writer raises) -----------------------


def test_routing_on_emit_failure_marks_audit(
    routing_on_client: TestClient, audit_path: Path, pending_path: Path
) -> None:
    def _raising_writer(_path: Path, _event: object) -> None:
        raise OSError("disk full")

    tv_router.set_pending_signal_writer(_raising_writer)
    try:
        body = json.dumps({"ticker": "BTCUSDT", "action": "buy"}).encode()
        resp = routing_on_client.post(
            "/tradingview/webhook",
            content=body,
            headers={_SIGNATURE_HEADER: _sign(body)},
        )
    finally:
        tv_router.reset_pending_signal_writer()

    assert resp.status_code == 202
    data = resp.json()
    assert "event_id" not in data  # no event id when emit failed

    audit_entry = json.loads(audit_path.read_text(encoding="utf-8").splitlines()[0])
    assert audit_entry["routing"]["status"] == "emit_failed"
    assert "disk full" in audit_entry["routing"]["reason"]
    assert audit_entry["provenance"]["version"] == "tv-1"
