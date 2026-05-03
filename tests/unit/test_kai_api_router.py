"""Tests for app.api.routers.kai."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routers.kai import router as kai_router
from app.audit.kai_audit_service import (
    KaiAuditService,
    reset_default_kai_audit_service,
)
from app.messaging.kai_persona import reset_persona_cache


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    reset_persona_cache()
    reset_default_kai_audit_service()
    # Redirect audit JSONL into tmp so tests don't pollute artifacts/.
    test_audit = tmp_path / "kai_audit.jsonl"

    from app.audit import kai_audit_service as audit_module

    monkeypatch.setattr(audit_module, "_default_service", KaiAuditService(audit_path=test_audit))

    app = FastAPI()
    app.include_router(kai_router)
    yield TestClient(app)

    reset_persona_cache()
    reset_default_kai_audit_service()


def test_persona_endpoint_returns_motto(client: TestClient):
    r = client.get("/api/kai/persona")
    assert r.status_code == 200
    body = r.json()
    assert body["motto"] == "Persona non grata"
    assert body["id"] == "kai"
    assert body["name"] == "KAI"
    assert "state_machine" in body


def test_state_endpoint_returns_idle_runtime(client: TestClient):
    r = client.get("/api/kai/state")
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "IDLE"
    assert "comment" in body
    assert body["statusLabel"] == "IDLE"


def test_audit_post_persists_event(client: TestClient):
    payload = {
        "type": "KAI_STATE_CHANGED",
        "state": "SIGNAL",
        "severity": "positive_watch",
        "source": "test",
        "message": "found something",
        "payload": {"asset": "BTC/USDT"},
    }
    r = client.post("/api/kai/audit", json=payload)
    assert r.status_code == 201
    body = r.json()
    assert body["id"].startswith("kai_")
    assert body["payload"] == {"asset": "BTC/USDT"}


def test_audit_post_rejects_invalid_event_type(client: TestClient):
    r = client.post(
        "/api/kai/audit",
        json={
            "type": "KAI_FAKE_EVENT",
            "state": "IDLE",
            "severity": "info",
            "source": "test",
            "message": "x",
        },
    )
    assert r.status_code == 400
    assert "invalid" in r.json()["detail"].lower()


def test_audit_post_rejects_invalid_state(client: TestClient):
    r = client.post(
        "/api/kai/audit",
        json={
            "type": "KAI_STATE_CHANGED",
            "state": "PARTY",
            "severity": "info",
            "source": "test",
            "message": "x",
        },
    )
    assert r.status_code == 400


def test_audit_get_returns_tail(client: TestClient):
    for i in range(3):
        client.post(
            "/api/kai/audit",
            json={
                "type": "KAI_STATE_CHANGED",
                "state": "IDLE",
                "severity": "info",
                "source": "test",
                "message": f"e{i}",
            },
        )
    r = client.get("/api/kai/audit?limit=5")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 3
    assert body["limit"] == 5
    assert body["events"][-1]["message"] == "e2"


def test_audit_get_clamps_limit(client: TestClient):
    r = client.get("/api/kai/audit?limit=99999")
    # FastAPI Query(le=1000) returns 422 for invalid range
    assert r.status_code == 422


def test_correlation_id_round_trips(client: TestClient):
    r = client.post(
        "/api/kai/audit",
        json={
            "type": "KAI_LIVETRADE_BLOCKED",
            "state": "WARNING",
            "severity": "critical",
            "source": "guard",
            "message": "blocked",
            "correlationId": "sig_abc123",
            "payload": {"reasons": ["x"]},
        },
    )
    assert r.status_code == 201
    assert r.json()["correlationId"] == "sig_abc123"
