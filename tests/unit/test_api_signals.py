"""Tests for the dashboard /signals/paste envelope endpoint."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routers import operator as operator_router
from app.api.routers import signals as signals_router
from app.core.settings import AppSettings


def _set_api_key(app: FastAPI, value: str) -> None:
    settings = AppSettings()
    settings.api_key = value
    app.dependency_overrides[operator_router.get_settings] = lambda: settings


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    audit = tmp_path / "envelope.jsonl"
    monkeypatch.setattr(signals_router, "_ENVELOPE_AUDIT_PATH", audit)

    test_app = FastAPI()
    test_app.include_router(signals_router.router)
    _set_api_key(test_app, "dashboard-token")

    with TestClient(test_app) as test_client:
        test_client.audit_path = audit  # type: ignore[attr-defined]
        yield test_client

    test_app.dependency_overrides.clear()


def _hdr() -> dict[str, str]:
    return {"Authorization": "Bearer dashboard-token"}


_SIGNAL_TEXT = (
    "[SIGNAL]\n"
    "Signal ID: SIG-20260415-BTCUSDT-999\n"
    "Source: Dashboard\n"
    "Exchange Scope: binance_futures\n"
    "Symbol: BTC/USDT\n"
    "Side: BUY\n"
    "Direction: LONG\n"
    "Entry Rule: BELOW 65000\n"
    "Targets: 70000\n"
    "Stop Loss: 62000\n"
    "Leverage: 10x\n"
    "Status: NEW\n"
    "Timestamp: 2026-04-15T10:00:00Z\n"
)

_NEWS_TEXT = (
    "[NEWS]\n"
    "Source: Dashboard\n"
    "Title: Market update\n"
    "Priority: Medium\n"
    "Timestamp: 2026-04-15T10:00:00Z\n"
)


def _audit_rows(client: TestClient) -> list[dict]:
    path: Path = client.audit_path  # type: ignore[attr-defined]
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_accepts_signal_and_writes_envelope_audit(client: TestClient) -> None:
    resp = client.post("/signals/paste", json={"text": _SIGNAL_TEXT}, headers=_hdr())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "accepted"
    assert body["stage"] == "accepted"
    assert body["message_type"] == "signal"
    assert body["envelope_id"].startswith("ENV-")
    assert len(body["idempotency_key"]) == 32

    rows = _audit_rows(client)
    assert len(rows) == 1
    row = rows[0]
    assert row["source"] == "dashboard"
    assert row["stage"] == "accepted"
    assert row["message_type"] == "signal"
    assert row["envelope_id"] == body["envelope_id"]
    assert row["idempotency_key"] == body["idempotency_key"]


def test_accepts_news_without_execution_path(client: TestClient) -> None:
    resp = client.post("/signals/paste", json={"text": _NEWS_TEXT}, headers=_hdr())
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "accepted"
    assert body["message_type"] == "news"


def test_rejects_text_without_header(client: TestClient) -> None:
    resp = client.post(
        "/signals/paste",
        json={"text": "No header here\nSymbol: BTC"},
        headers=_hdr(),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "rejected"
    assert body["stage"] == "parse"
    assert body["envelope_id"] is None
    assert body["errors"]

    rows = _audit_rows(client)
    assert rows[0]["stage"] == "parse"
    assert rows[0]["status"] == "rejected"


def test_second_identical_signal_is_flagged_duplicate(client: TestClient) -> None:
    first = client.post("/signals/paste", json={"text": _SIGNAL_TEXT}, headers=_hdr())
    second = client.post("/signals/paste", json={"text": _SIGNAL_TEXT}, headers=_hdr())

    assert first.json()["status"] == "accepted"
    dup = second.json()
    assert dup["status"] == "duplicate"
    assert dup["stage"] == "idempotency_gate"
    assert dup["idempotency_key"] == first.json()["idempotency_key"]

    rows = _audit_rows(client)
    assert sum(1 for r in rows if r["stage"] == "accepted") == 1
    assert sum(1 for r in rows if r["stage"] == "idempotency_gate") == 1


def test_missing_required_signal_fields_blocked_at_execution_gate(client: TestClient) -> None:
    incomplete = (
        "[SIGNAL]\n"
        "Source: Dashboard\n"
        "Symbol: BTC/USDT\n"
        "Direction: LONG\n"
        "Targets: 70000\n"
        "Entry Rule: BELOW 74700\n"
    )
    resp = client.post("/signals/paste", json={"text": incomplete}, headers=_hdr())
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "rejected"
    # Either execution_gate (missing stop_loss/exchange_scope) or schema_validation
    assert body["stage"] in {"execution_gate", "schema_validation"}
    assert body["errors"]


def test_requires_bearer_token(client: TestClient) -> None:
    resp = client.post("/signals/paste", json={"text": _SIGNAL_TEXT})
    assert resp.status_code == 401


def test_rejects_wrong_bearer_token(client: TestClient) -> None:
    resp = client.post(
        "/signals/paste",
        json={"text": _SIGNAL_TEXT},
        headers={"Authorization": "Bearer wrong"},
    )
    assert resp.status_code == 403


# --------------------------------------------------------------------------- #
# P13a: GET /signals/envelope/recent                                          #
# --------------------------------------------------------------------------- #


def test_recent_envelopes_empty_when_no_audit(client: TestClient) -> None:
    resp = client.get("/signals/envelope/recent", headers=_hdr())
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"count": 0, "records": []}


def test_recent_envelopes_returns_newest_first(client: TestClient) -> None:
    first = client.post("/signals/paste", json={"text": _NEWS_TEXT}, headers=_hdr())
    second = client.post("/signals/paste", json={"text": _SIGNAL_TEXT}, headers=_hdr())
    assert first.status_code == 200 and second.status_code == 200

    resp = client.get("/signals/envelope/recent", headers=_hdr())
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 2
    # Newest first — signal (2nd write) before news (1st write)
    assert body["records"][0]["message_type"] == "signal"
    assert body["records"][1]["message_type"] == "news"
    assert body["records"][0]["envelope_id"] == second.json()["envelope_id"]


def test_recent_envelopes_respects_limit(client: TestClient) -> None:
    for suffix in ("001", "002", "003"):
        text = _SIGNAL_TEXT.replace("SIG-20260415-BTCUSDT-999", f"SIG-20260415-BTCUSDT-{suffix}")
        resp = client.post("/signals/paste", json={"text": text}, headers=_hdr())
        assert resp.status_code == 200

    resp = client.get("/signals/envelope/recent?limit=2", headers=_hdr())
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 2
    assert len(body["records"]) == 2


def test_recent_envelopes_limit_bounds_enforced(client: TestClient) -> None:
    resp = client.get("/signals/envelope/recent?limit=0", headers=_hdr())
    assert resp.status_code == 422
    resp = client.get("/signals/envelope/recent?limit=500", headers=_hdr())
    assert resp.status_code == 422


def test_recent_envelopes_requires_bearer_token(client: TestClient) -> None:
    resp = client.get("/signals/envelope/recent")
    assert resp.status_code == 401


def test_recent_envelopes_rejects_wrong_bearer(client: TestClient) -> None:
    resp = client.get(
        "/signals/envelope/recent",
        headers={"Authorization": "Bearer wrong"},
    )
    assert resp.status_code == 403
