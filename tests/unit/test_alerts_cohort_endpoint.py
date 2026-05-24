"""Tests for GET /alerts/auto-annotate-report (DALI-P-102).

Covers:
- Happy path: 4 cohort splits returned, structure matches Pydantic schema
- Default 7d window when no since/until supplied
- Explicit since/until filtering
- Invalid since string → 400
- Empty audit_dir → all cohorts zero
- dispatched_window=true triggers fresh_dispatch join with missing_audit counter
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.alerts.audit import (
    AlertAuditRecord,
    AlertOutcomeAnnotation,
    append_alert_audit,
    append_outcome_annotation,
)
from app.api.routers.alerts import get_audit_dir, router


def _make_app(audit_dir: Path) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_audit_dir] = lambda: audit_dir
    return app


def _outcome(doc_id: str, outcome: str, note: str, ts: str) -> AlertOutcomeAnnotation:
    return AlertOutcomeAnnotation(
        document_id=doc_id,
        outcome=outcome,  # type: ignore[arg-type]
        annotated_at=ts,
        asset="BTC/USDT",
        note=note,
    )


def test_endpoint_returns_all_six_cohorts(tmp_path: Path) -> None:
    now = datetime.now(UTC).isoformat()
    for o in (
        _outcome("d1", "hit", "auto: bullish", now),
        _outcome("d2", "miss", "auto: bearish", now),
        _outcome("d3", "hit", "backfill: bullish", now),
        _outcome("d4", "miss", "reeval: bearish", now),
        _outcome("d5", "hit", "legacy note", now),
    ):
        append_outcome_annotation(o, tmp_path)

    client = TestClient(_make_app(tmp_path))
    resp = client.get("/alerts/auto-annotate-report")
    assert resp.status_code == 200

    body = resp.json()
    cohorts = body["cohorts"]
    assert set(cohorts.keys()) == {
        "fresh_auto", "backfill", "reeval", "other",
        "latest_per_doc", "fresh_dispatch",
    }
    assert cohorts["fresh_auto"]["total"] == 2
    assert cohorts["backfill"]["total"] == 1
    assert cohorts["reeval"]["total"] == 1
    assert cohorts["other"]["total"] == 1
    assert cohorts["fresh_auto"]["hit_rate_pct"] == 50.0
    assert body["window"]["timestamp_basis"] == "annotated_at"
    assert body["generated_at"]


def test_default_window_is_last_7d(tmp_path: Path) -> None:
    """When no since/until is supplied, outcomes older than 7d must be excluded."""
    inside = datetime.now(UTC).isoformat()
    outside = (datetime.now(UTC) - timedelta(days=10)).isoformat()
    append_outcome_annotation(_outcome("d_inside", "hit", "auto: x", inside), tmp_path)
    append_outcome_annotation(_outcome("d_outside", "hit", "auto: x", outside), tmp_path)

    client = TestClient(_make_app(tmp_path))
    body = client.get("/alerts/auto-annotate-report").json()
    assert body["cohorts"]["fresh_auto"]["total"] == 1


def test_explicit_window_filters_outcomes(tmp_path: Path) -> None:
    t1 = "2026-05-20T12:00:00+00:00"
    t2 = "2026-05-22T12:00:00+00:00"
    append_outcome_annotation(_outcome("d1", "hit", "auto: x", t1), tmp_path)
    append_outcome_annotation(_outcome("d2", "hit", "auto: x", t2), tmp_path)

    client = TestClient(_make_app(tmp_path))
    body = client.get(
        "/alerts/auto-annotate-report",
        params={"since": "2026-05-21T00:00:00+00:00"},
    ).json()
    assert body["cohorts"]["fresh_auto"]["total"] == 1


def test_invalid_since_returns_400(tmp_path: Path) -> None:
    client = TestClient(_make_app(tmp_path))
    resp = client.get(
        "/alerts/auto-annotate-report",
        params={"since": "not-a-date"},
    )
    assert resp.status_code == 400
    assert "Invalid 'since'" in resp.json()["detail"]


def test_empty_audit_dir_returns_zero_cohorts(tmp_path: Path) -> None:
    client = TestClient(_make_app(tmp_path))
    body = client.get("/alerts/auto-annotate-report").json()
    for name in ("fresh_auto", "backfill", "reeval", "other"):
        assert body["cohorts"][name]["total"] == 0
        assert body["cohorts"][name]["hit_rate_pct"] is None
    assert body["raw_rows"] == 0


def test_dispatched_window_populates_fresh_dispatch(tmp_path: Path) -> None:
    """dispatched_window=true joins outcomes with alert_audit + counts missing_audit."""
    inside = datetime.now(UTC).isoformat()
    append_alert_audit(
        AlertAuditRecord(
            document_id="doc_with_audit",
            channel="telegram",
            message_id="1",
            is_digest=False,
            dispatched_at=inside,
        ),
        tmp_path,
    )
    append_outcome_annotation(_outcome("doc_with_audit", "hit", "auto: x", inside), tmp_path)
    append_outcome_annotation(_outcome("doc_without_audit", "hit", "auto: x", inside), tmp_path)

    client = TestClient(_make_app(tmp_path))
    body = client.get(
        "/alerts/auto-annotate-report",
        params={"dispatched_window": "true"},
    ).json()
    fd = body["cohorts"]["fresh_dispatch"]
    assert fd["total"] == 1
    assert fd["hit"] == 1
    assert fd["missing_audit"] == 1
    assert body["window"]["timestamp_basis"] == "dispatched_at"
