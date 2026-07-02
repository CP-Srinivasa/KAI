"""KYT integration: flag-gated execution gate + read API.

Verifies the non-breaking contract (disabled → no-op), enforce vs shadow mode,
and the read-only dashboard/reporting endpoints.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

import app.security.kyt.gate as gate
from app.api.routers import kyt as kyt_api


@dataclass
class _FakeKytSettings:
    enabled: bool = True
    shadow_only: bool = False
    behavioral_enabled: bool = True
    provider: str = "local_lists"
    fail_mode: str = "conservative"

    @property
    def mode(self) -> str:
        return "enforce" if (self.enabled and not self.shadow_only) else "shadow"


def _patch_gate(monkeypatch: pytest.MonkeyPatch, settings: _FakeKytSettings) -> None:
    monkeypatch.setattr(gate, "_kyt_settings", lambda: settings)
    # keep the test hermetic — no real audit / agent-dropbox writes
    monkeypatch.setattr(gate, "write_assessment", lambda *a, **k: None)
    monkeypatch.setattr(gate, "emit_agent_alerts", lambda *a, **k: [])
    monkeypatch.setattr(gate, "load_recent_history", lambda *a, **k: [])


def test_gate_disabled_is_noop() -> None:
    # No patching → default settings → kyt disabled → None (non-breaking).
    assert (
        gate.screen_order(
            tx_id="t1",
            symbol="XMR/USDT",
            venue="paper",
            side="buy",
            quantity=1.0,
            entry_price=150.0,
        )
        is None
    )


def test_gate_enforce_blocks_privacy_coin(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_gate(monkeypatch, _FakeKytSettings(enabled=True, shadow_only=False))
    a = gate.screen_order(
        tx_id="t2",
        symbol="XMR/USDT",
        venue="paper",
        side="buy",
        quantity=1.0,
        entry_price=150.0,
    )
    assert a is not None
    assert gate.enforce_blocks(a) is True  # enforce + manual_review blocks


def test_gate_shadow_does_not_block(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_gate(monkeypatch, _FakeKytSettings(enabled=True, shadow_only=True))
    a = gate.screen_order(
        tx_id="t3",
        symbol="XMR/USDT",
        venue="paper",
        side="buy",
        quantity=1.0,
        entry_price=150.0,
    )
    assert a is not None  # still assessed
    assert gate.enforce_blocks(a) is False  # shadow never blocks


def test_gate_clean_order_does_not_block(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_gate(monkeypatch, _FakeKytSettings(enabled=True, shadow_only=False))
    a = gate.screen_order(
        tx_id="t4",
        symbol="BTC/USDT",
        venue="paper",
        side="buy",
        quantity=0.1,
        entry_price=70000.0,
    )
    assert a is not None
    assert gate.enforce_blocks(a) is False


# --- read API --------------------------------------------------------------


def _seed_audit(path: Path) -> None:
    rows = [
        {
            "tx_id": "a",
            "decision": "allow",
            "risk_level": "low",
            "score": 0,
            "reason_codes": ["ok"],
        },
        {
            "tx_id": "b",
            "decision": "block",
            "risk_level": "critical",
            "score": 100,
            "reason_codes": ["sanctioned_entity"],
            "context": {"symbol": "SCAM/USDT"},
        },
        {
            "tx_id": "c",
            "decision": "manual_review",
            "risk_level": "high",
            "score": 75,
            "reason_codes": ["privacy_coin"],
            "context": {"symbol": "XMR/USDT"},
        },
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


@pytest.mark.asyncio
async def test_api_status_and_open_reviews(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    audit = tmp_path / "kyt.jsonl"
    _seed_audit(audit)
    monkeypatch.setattr(kyt_api, "_KYT_AUDIT", audit)

    status = await kyt_api.kyt_status()
    assert status["assessments_seen"] == 3
    assert status["by_decision"]["block"] == 1

    reviews = await kyt_api.kyt_open_reviews()
    assert reviews["count"] == 2  # block + manual_review, not allow
    decisions = {r["decision"] for r in reviews["open_reviews"]}
    assert decisions == {"block", "manual_review"}

    tx = await kyt_api.kyt_transaction("c")
    assert tx["found"] is True
    assert tx["latest"]["decision"] == "manual_review"


@pytest.mark.asyncio
async def test_api_missing_audit_is_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(kyt_api, "_KYT_AUDIT", tmp_path / "absent.jsonl")
    status = await kyt_api.kyt_status()
    assert status["assessments_seen"] == 0
    assert status["audit_present"] is False
    reviews = await kyt_api.kyt_open_reviews()
    assert reviews["count"] == 0
