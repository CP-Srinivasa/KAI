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
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
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


def test_missing_required_signal_fields_goes_to_completion_gate(client: TestClient) -> None:
    # Only completable fields missing (exchange_scope, stop_loss, leverage)
    # → status should be needs_completion, NOT rejected.
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
    assert body["status"] == "needs_completion"
    assert body["stage"] == "completion_gate"
    assert body["envelope_id"] is None  # no envelope yet — operator must complete
    assert "exchange_scope" in body["missing_fields"]
    assert "stop_loss" in body["missing_fields"]
    assert body["parsed_preview"]["symbol"] in {"BTC/USDT", "BTCUSDT"}


def test_freeform_signal_without_exchange_asks_for_completion(client: TestClient) -> None:
    # Free-form Telegram-group paste, no exchange named.
    text = (
        "Long/Buy #USELESS/USDT\n"
        "Entry Point - 4340\n"
        "Targets: 4360 - 4385 - 4405 - 4425\n"
        "Leverage - 10x\n"
        "Stop Loss - 4160\n"
    )
    resp = client.post("/signals/paste", json={"text": text}, headers=_hdr())
    body = resp.json()
    assert body["status"] == "needs_completion"
    assert body["stage"] == "completion_gate"
    assert body["missing_fields"] == ["exchange_scope"]
    preview = body["parsed_preview"]
    assert preview["symbol"] == "USELESS/USDT"
    assert preview["stop_loss"] == 4160
    assert preview["leverage"] == 10
    assert preview["targets"] == [4360, 4385, 4405, 4425]
    assert preview["exchange_scope"] == []


def test_freeform_signal_with_completion_fields_accepted(client: TestClient) -> None:
    # Same paste — this time the operator supplies the exchange.
    text = (
        "Long/Buy #USELESS/USDT\n"
        "Entry Point - 4340\n"
        "Targets: 4360 - 4385 - 4405 - 4425\n"
        "Leverage - 10x\n"
        "Stop Loss - 4160\n"
    )
    resp = client.post(
        "/signals/paste",
        json={
            "text": text,
            "completion_fields": {"exchange_scope": ["Binance Futures"]},
        },
        headers=_hdr(),
    )
    body = resp.json()
    assert body["status"] == "accepted"
    assert body["stage"] == "accepted"
    assert body["message_type"] == "signal"
    assert body["envelope_id"].startswith("ENV-")
    rows = _audit_rows(client)
    payload = rows[-1]["payload"]
    assert payload["exchange_scope"] == ["binance_futures"]


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
    assert body["count"] == 0
    assert body["records"] == []


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


# --------------------------------------------------------------------------- #
# F-027: payload → signal projection                                          #
# --------------------------------------------------------------------------- #


def test_recent_envelopes_projects_signal_payload(client: TestClient) -> None:
    resp = client.post("/signals/paste", json={"text": _SIGNAL_TEXT}, headers=_hdr())
    assert resp.status_code == 200

    listing = client.get("/signals/envelope/recent", headers=_hdr())
    assert listing.status_code == 200
    record = listing.json()["records"][0]
    assert record["message_type"] == "signal"
    signal = record["signal"]
    assert signal is not None
    assert signal["signal_id"] == "SIG-20260415-BTCUSDT-999"
    assert signal["symbol"] == "BTC/USDT"
    assert signal["direction"] == "long"
    assert signal["side"] == "buy"
    assert signal["exchange_scope"] == ["binance_futures"]
    assert signal["entry_type"] == "below"
    assert signal["entry_value"] == 65000.0
    assert signal["targets"] == [70000.0]
    assert signal["stop_loss"] == 62000.0
    assert signal["leverage"] == 10
    assert signal["signal_status"] == "new"
    assert signal["signal_timestamp"] is not None


def test_recent_envelopes_signal_null_for_news(client: TestClient) -> None:
    resp = client.post("/signals/paste", json={"text": _NEWS_TEXT}, headers=_hdr())
    assert resp.status_code == 200

    listing = client.get("/signals/envelope/recent", headers=_hdr())
    assert listing.status_code == 200
    record = listing.json()["records"][0]
    assert record["message_type"] == "news"
    assert record["signal"] is None


def _premium_raw(sig_id: str, ts: str) -> dict:
    return {
        "timestamp_utc": ts,
        "event": "telegram_channel_envelope",
        "message_type": "signal",
        "stage": "accepted",
        "status": "ok",
        "source": "telegram_premium_channel",
        "envelope_id": "ENV-TG-1",
        "source_uid": "telegram:-100:5",
        "message_id": 5,
        "payload": {
            "signal_id": sig_id,
            "source": "telegram_premium_channel",
            "symbol": "SKYAIUSDT",
            "display_symbol": "SKYAI/USDT",
            "side": "buy",
            "direction": "long",
            "entry_value": 24800.0,
            "source_uid": "telegram:-100:5",
            "source_message_id": 5,
            "timestamp_utc": ts,
        },
    }


def _premium_approved(sig_id: str, ts: str) -> dict:
    rec = _premium_raw(sig_id, ts)
    rec["event"] = "telegram_channel_approval"
    rec["source"] = "telegram_premium_channel_approved"
    rec["envelope_id"] = "ENV-APP-1"
    rec["origin_envelope_id"] = "ENV-TG-1"
    rec["payload"]["source"] = "telegram_premium_channel_approved"
    return rec


def test_recent_envelopes_dedupes_raw_and_approved(client: TestClient) -> None:
    """Dashboard double-count fix: raw + approved of the SAME signal collapse to
    ONE row, flagged double_sourced so the UI groups them as Rohsignal+Approved."""
    audit_path: Path = client.audit_path  # type: ignore[attr-defined]
    sig = "SIG-TGCH-DEDUPE-SKYAIUSDT"
    lines = [
        _premium_raw(sig, "2026-06-06T15:33:29+00:00"),
        _premium_approved(sig, "2026-06-06T15:33:30+00:00"),
    ]
    audit_path.write_text(
        "\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8"
    )

    listing = client.get("/signals/envelope/recent", headers=_hdr())
    assert listing.status_code == 200
    body = listing.json()
    assert body["count"] == 1, body  # raw+approved counted ONCE
    assert body["deduped_from"] == 2
    rec = body["records"][0]
    assert rec["double_sourced"] is True
    assert rec["has_raw_event"] is True
    assert rec["has_approved_event"] is True
    assert rec["merged_event_count"] == 2
    # canonical row is the approved (actionable) one
    assert rec["source"] == "telegram_premium_channel_approved"
    assert rec["dedup_key"] == f"sig:{sig}"
