"""Dashboard-Endpoint /dashboard/api/bayes-audit (Schatten-Vergleich-Spalte)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routers import dashboard as dashboard_mod
from app.api.routers.dashboard import router
from app.signals.bayes_journal import append_bayes_report
from app.signals.bayesian_confidence import (
    build_default_engine,
    build_news_evidence,
    build_volume_evidence,
)


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


def _seed(audit_path: Path, count: int) -> None:
    engine = build_default_engine()
    for i in range(count):
        report = engine.evaluate(
            [
                build_news_evidence(
                    relevance=0.5 + 0.05 * i,
                    sentiment_aligned_with_signal=True,
                ),
                build_volume_evidence(
                    volume_zscore=1.0,
                    price_move_aligned_with_signal=True,
                ),
            ],
            prior_probability=0.5,
        )
        append_bayes_report(
            decision_id=f"dec_{i:03d}",
            symbol="BTC/USDT",
            direction="long",
            report=report,
            path=audit_path,
        )


@pytest.fixture()
def audit_path(tmp_path: Path) -> Path:
    return tmp_path / "bayes_confidence_audit.jsonl"


def test_returns_empty_when_audit_missing(audit_path: Path) -> None:
    app = _make_app()
    with patch.object(dashboard_mod, "_BAYES_AUDIT", audit_path):
        with TestClient(app) as client:
            r = client.get("/dashboard/api/bayes-audit")
    assert r.status_code == 200
    body = r.json()
    assert body["entries"] == []
    assert body["total_count"] == 0


def test_returns_compacted_entries(audit_path: Path) -> None:
    _seed(audit_path, count=3)
    app = _make_app()
    with patch.object(dashboard_mod, "_BAYES_AUDIT", audit_path):
        with TestClient(app) as client:
            r = client.get("/dashboard/api/bayes-audit")
    assert r.status_code == 200
    body = r.json()
    assert body["total_count"] == 3
    assert body["returned_count"] == 3
    # Newest first
    assert [e["decision_id"] for e in body["entries"]] == ["dec_002", "dec_001", "dec_000"]
    # Shape contract
    e = body["entries"][0]
    for key in (
        "prior_probability",
        "posterior_probability",
        "confidence_score",
        "uncertainty_score",
        "evidence_weight",
        "agreement",
        "increased_count",
        "decreased_count",
        "neutral_count",
        "discarded_count",
        "residual_uncertainty_drivers",
    ):
        assert key in e
    assert isinstance(e["residual_uncertainty_drivers"], list)


def test_limit_param_caps_returned_entries(audit_path: Path) -> None:
    _seed(audit_path, count=10)
    app = _make_app()
    with patch.object(dashboard_mod, "_BAYES_AUDIT", audit_path):
        with TestClient(app) as client:
            r = client.get("/dashboard/api/bayes-audit?limit=3")
    body = r.json()
    assert body["total_count"] == 10
    assert body["returned_count"] == 3
    assert body["limit"] == 3
    assert [e["decision_id"] for e in body["entries"]] == ["dec_009", "dec_008", "dec_007"]


def test_limit_zero_clamped_to_one(audit_path: Path) -> None:
    _seed(audit_path, count=5)
    app = _make_app()
    with patch.object(dashboard_mod, "_BAYES_AUDIT", audit_path):
        with TestClient(app) as client:
            r = client.get("/dashboard/api/bayes-audit?limit=0")
    assert r.status_code == 200
    assert r.json()["limit"] == 1


def test_limit_above_max_clamped(audit_path: Path) -> None:
    _seed(audit_path, count=5)
    app = _make_app()
    with patch.object(dashboard_mod, "_BAYES_AUDIT", audit_path):
        with TestClient(app) as client:
            r = client.get("/dashboard/api/bayes-audit?limit=99999")
    body = r.json()
    assert body["limit"] == 500
    assert body["returned_count"] == 5  # nur 5 vorhanden


def test_response_has_no_store_cache_header(audit_path: Path) -> None:
    app = _make_app()
    with patch.object(dashboard_mod, "_BAYES_AUDIT", audit_path):
        with TestClient(app) as client:
            r = client.get("/dashboard/api/bayes-audit")
    assert "no-store" in r.headers.get("cache-control", "")
