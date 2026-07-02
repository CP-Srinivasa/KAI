"""V4.1 regression guard: position-repair close must emit position_closed event.

Before this fix the operator-triggered close path (Portfolio UI "Schliessen"
button -> /api/premium-signals/position-repair action=close) wrote ONLY an
order_filled-sell event. The V4 Bayes-Posterior pipeline relies on
position_closed events to detect per-trade outcomes - without them all
premium-signal trades stayed in the UNSOURCED ``tradingloop`` bucket and
the learning loop was blind.

Contract being protected:
1. After a successful close, the paper_execution_audit stream contains
   a position_closed event with reason="manual".
2. The event carries trade_pnl_usd, fee_usd, position_side (the V4 contract).
3. The response payload exposes trade_pnl_usd + fee_usd so the UI/operator
   can confirm the outcome before the next API call.
4. Idempotency: a second call with the same idempotency-key returns
   the cached response and does NOT re-emit the event.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.execution.paper_engine_singleton import (
    get_paper_engine,
    reset_paper_engine_cache,
)


def _read_audit_events(audit_path: Path) -> list[dict]:
    if not audit_path.exists():
        return []
    out: list[dict] = []
    with audit_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


@pytest.fixture
def fresh_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Reset the paper-engine singleton to a clean tmp-based audit path."""
    # Engine writes audit relative to cwd -> chdir to tmp isolates the stream.
    monkeypatch.chdir(tmp_path)
    audit_path = tmp_path / "artifacts" / "paper_execution_audit.jsonl"
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    reset_paper_engine_cache()
    eng = get_paper_engine()
    yield eng, audit_path
    reset_paper_engine_cache()


def test_position_repair_close_emits_position_closed_event(fresh_engine) -> None:
    """The V4.1 contract: close action must emit position_closed for V4 to learn."""
    eng, audit_path = fresh_engine

    # Open a position the operator could later close manually.
    open_order = eng.create_order(
        symbol="BAS/USDT",
        side="buy",
        quantity=1000.0,
        order_type="limit",
        limit_price=0.025,
        idempotency_key="open-BAS",
        position_side="long",
        source="telegram_premium_channel_approved",
        leverage=10.0,
    )
    eng.fill_order(open_order, 0.025)
    assert "BAS/USDT" in eng.portfolio.positions

    # Drive the position-repair endpoint.
    app = create_app()
    client = TestClient(app)
    resp = client.post(
        "/api/premium-signals/position-repair",
        json={
            "symbol": "BAS/USDT",
            "action": "close",
            "idempotency_key": "close-bas-1",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "closed"
    assert body["symbol"] == "BAS/USDT"
    # V4.1 surface: PnL fields exposed in response so caller can confirm.
    assert "trade_pnl_usd" in body
    assert "fee_usd" in body

    # The audit stream must now contain a position_closed event with
    # reason="manual" and the V4 contract fields.
    events = _read_audit_events(audit_path)
    closes = [e for e in events if e.get("event_type") == "position_closed"]
    assert len(closes) == 1, (
        f"expected exactly one position_closed event, got {len(closes)}: {events}"
    )
    close_event = closes[0]
    assert close_event["symbol"] == "BAS/USDT"
    assert close_event["reason"] == "manual"
    assert close_event["position_side"] == "long"
    # V4 reads trade_pnl_usd (NOT realized_pnl_usd - see paper_audit_pnl
    # field semantics memory).
    assert "trade_pnl_usd" in close_event
    assert "fee_usd" in close_event


def test_position_repair_close_is_idempotent_and_no_duplicate_event(
    fresh_engine,
) -> None:
    """Second call with same idempotency-key returns cache, does NOT re-close."""
    eng, audit_path = fresh_engine
    open_order = eng.create_order(
        symbol="ASTER/USDT",
        side="buy",
        quantity=100.0,
        order_type="limit",
        limit_price=0.68,
        idempotency_key="open-ASTER",
        position_side="long",
        source="telegram_premium_channel_approved",
        leverage=10.0,
    )
    eng.fill_order(open_order, 0.68)

    app = create_app()
    client = TestClient(app)
    body = {
        "symbol": "ASTER/USDT",
        "action": "close",
        "idempotency_key": "close-aster-1",
    }
    first = client.post("/api/premium-signals/position-repair", json=body)
    second = client.post("/api/premium-signals/position-repair", json=body)
    assert first.status_code == 200
    assert second.status_code == 200
    # Second response is the cached one (idempotency layer adds the marker).
    assert second.json().get("_idempotency_cached") is True

    events = _read_audit_events(audit_path)
    closes = [e for e in events if e.get("event_type") == "position_closed"]
    # Critically: only ONE position_closed event, not two.
    assert len(closes) == 1, f"idempotency must prevent duplicate close events, got {len(closes)}"


def test_position_repair_returns_404_when_no_open_position(fresh_engine) -> None:
    """Sanity: closing a non-open symbol returns HTTP 404, not silent success."""
    _eng, audit_path = fresh_engine
    app = create_app()
    client = TestClient(app)
    resp = client.post(
        "/api/premium-signals/position-repair",
        json={
            "symbol": "NEVER_OPENED/USDT",
            "action": "close",
            "idempotency_key": "close-noop",
        },
    )
    assert resp.status_code == 404
    # And no spurious position_closed events landed in the audit stream.
    events = _read_audit_events(audit_path)
    closes = [e for e in events if e.get("event_type") == "position_closed"]
    assert closes == []
