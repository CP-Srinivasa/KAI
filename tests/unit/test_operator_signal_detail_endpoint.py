"""HTTP-level tests for GET /operator/signals/{id} (+ /explain).

Verifies 200 for a known signal, 404 for an unknown one, and that the route is
read-only (auth-gated, no trading semantics).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routers.operator import router
from app.core.settings import get_settings

AUTH = {"Authorization": "Bearer test-key"}


def _make_app(api_key: str = "test-key") -> FastAPI:
    app = FastAPI()
    app.include_router(router)

    def _override_settings() -> SimpleNamespace:
        return SimpleNamespace(api_key=api_key, cf_access_allowed_emails="")

    app.dependency_overrides[get_settings] = _override_settings
    return app


def test_signal_detail_200_for_known_id() -> None:
    c = TestClient(_make_app())
    with patch(
        "app.decisions.signal_detail.build_signal_detail",
        return_value={"report_type": "operator_signal_detail", "signal_id": "DS-1"},
    ):
        r = c.get("/operator/signals/DS-1", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["signal_id"] == "DS-1"


def test_signal_detail_404_for_unknown_id() -> None:
    c = TestClient(_make_app())
    with patch("app.decisions.signal_detail.build_signal_detail", return_value=None):
        r = c.get("/operator/signals/NOPE", headers=AUTH)
    assert r.status_code == 404


def test_signal_detail_503_on_malformed_journal() -> None:
    c = TestClient(_make_app())
    with patch(
        "app.decisions.signal_detail.build_signal_detail",
        side_effect=ValueError("malformed"),
    ):
        r = c.get("/operator/signals/DS-1", headers=AUTH)
    assert r.status_code == 503


def test_signal_explain_200_and_404() -> None:
    c = TestClient(_make_app())
    with patch(
        "app.decisions.signal_detail.build_signal_explain",
        return_value={"report_type": "operator_signal_explain", "signal_id": "DS-1"},
    ):
        ok = c.get("/operator/signals/DS-1/explain", headers=AUTH)
    assert ok.status_code == 200
    with patch("app.decisions.signal_detail.build_signal_explain", return_value=None):
        missing = c.get("/operator/signals/NOPE/explain", headers=AUTH)
    assert missing.status_code == 404


def test_signal_detail_requires_auth() -> None:
    c = TestClient(_make_app(api_key="real-key"))
    r = c.get("/operator/signals/DS-1")  # no Authorization header
    assert r.status_code == 401
