"""STAB-02 — `/health` nennt den laufenden Commit und den Abstand zum Checkout.

Vorher: `{"status":"ok","version":"0.1.0"}` — ein Server, der 7 Tage hinter seinem
eigenen Checkout lief, sah exakt so aus wie einer auf dem aktuellen Stand.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.api.main import app
from app.api.routers import health as health_router
from app.core import runtime_identity as ri

NOW = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)


def _identity(commit: str | None) -> ri.RuntimeIdentity:
    return ri.RuntimeIdentity(
        schema=ri.SCHEMA,
        runtime_commit=commit,
        started_at_utc="2026-08-18T20:30:09+00:00",
        lock_sha256_at_start="e" * 64,
        pid=2736616,
    )


def test_health_exposes_runtime_and_checkout_commit(monkeypatch) -> None:
    monkeypatch.setattr(health_router, "get_runtime_identity", lambda: _identity("7" * 40))
    monkeypatch.setattr(
        health_router,
        "drift_report",
        lambda identity, **_k: {
            "runtime_commit": identity.runtime_commit,
            "checkout_commit": "5" * 40,
            "drift_commits": 23,
            "started_at_utc": identity.started_at_utc,
            "uptime_s": 604800.0,
            "lock_changed": False,
        },
    )
    body = TestClient(app).get("/health").json()
    assert body["status"] == "ok"
    assert body["version"] == "0.1.0"
    assert body["runtime_commit"] == "7" * 40
    assert body["checkout_commit"] == "5" * 40
    assert body["drift_commits"] == 23
    assert body["started_at_utc"] == "2026-08-18T20:30:09+00:00"
    assert body["uptime_s"] == 604800.0
    assert body["lock_changed"] is False


def test_health_stays_ok_when_identity_is_unavailable(monkeypatch) -> None:
    def boom() -> ri.RuntimeIdentity:
        raise RuntimeError("git exploded")

    monkeypatch.setattr(health_router, "get_runtime_identity", boom)
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["runtime_commit"] is None
    assert body["drift_commits"] is None


def test_health_reports_real_identity_of_this_checkout() -> None:
    # Kein Mock: im Test-Prozess ist Runtime == Checkout, Drift 0 (oder None ohne git).
    ri.reset_runtime_identity_for_tests()
    body = TestClient(app).get("/health").json()
    if body["runtime_commit"] is not None:
        assert body["runtime_commit"] == body["checkout_commit"]
        assert body["drift_commits"] == 0
    assert body["started_at_utc"] is not None
