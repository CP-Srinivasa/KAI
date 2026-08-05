"""Sprint 5 — value-action control endpoint (plan/execute, policy + B-005, inert).

Covers: plan mode returns the plan + policy verdict + plan_hash; execute is denied
for a disallowed action; an in-envelope auto_execute runs straight through but stays
INERT (pay_enabled off → disabled); a needs_confirm execute with a mismatched
plan-hash is rejected (B-005) WITHOUT touching the node.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routers import ln_control as lc
from app.lightning.adapter import LightningBalanceSnapshot
from app.lightning.policy import PolicyEnvelope

_URL = "/dashboard/api/ln/value-action"


def _app() -> FastAPI:
    a = FastAPI()
    a.include_router(lc.router)
    return a


async def _bal_million() -> LightningBalanceSnapshot:
    return LightningBalanceSnapshot(state="ok", available_balance_sat=1_000_000)


async def _spend_reconciled(**kwargs) -> dict[str, object]:
    return {
        "ledger_spent_sat": 0,
        "lnd_spent_sat": 0,
        "effective_spent_sat": 0,
        "gap_sat": 0,
        "available": True,
    }


def _patch(monkeypatch, envelope: PolicyEnvelope) -> None:
    lc.reset_control_state()
    monkeypatch.setattr(lc.PolicyStore, "load", lambda self: envelope)
    monkeypatch.setattr(lc, "get_fresh_available_balance", _bal_million)
    monkeypatch.setattr(lc, "reconcile_spent_today", _spend_reconciled)
    # Isolate the daily-cap input from the shared ops ledger (other tests append
    # executed spends to the default path) so the policy decision is deterministic.
    monkeypatch.setattr(lc, "spent_today_sat", lambda: 0)


def test_plan_mode_returns_plan_decision_and_hash(monkeypatch) -> None:
    _patch(monkeypatch, PolicyEnvelope.default())  # deny everything
    r = TestClient(_app()).post(
        _URL, json={"action": "send_coins", "params": {"addr": "bc1q", "amount_sat": 1000}}
    )
    assert r.status_code == 200
    b = r.json()
    assert b["mode"] == "plan"
    assert b["policy"]["decision"] == "denied"  # default envelope denies
    assert len(b["plan_hash"]) == 64
    assert b["plan"]["state"] == "disabled"  # inert: pay_enabled off → node never touched


def test_execute_denied_for_disallowed_action(monkeypatch) -> None:
    _patch(monkeypatch, PolicyEnvelope.default())
    r = TestClient(_app()).post(
        _URL,
        json={
            "action": "send_coins",
            "params": {"addr": "bc1q", "amount_sat": 1000},
            "confirm": {"hotp": "x", "plan_hash": "y", "idempotency_key": "k"},
        },
    )
    assert r.status_code == 403 and "policy denied" in r.json()["detail"]


def test_offchain_spend_denied_when_listpayments_reconcile_unavailable(monkeypatch) -> None:
    env = PolicyEnvelope(
        allowed_actions=frozenset({"keysend"}), per_action_cap_sat=10_000, daily_cap_sat=50_000
    )
    _patch(monkeypatch, env)

    async def unavailable(**kwargs) -> dict[str, object]:
        return {"effective_spent_sat": 0, "gap_sat": None, "available": False}

    monkeypatch.setattr(lc, "reconcile_spent_today", unavailable)
    params = {"dest_pubkey_hex": "02ab", "amt_sat": 1000}
    response = TestClient(_app()).post(
        _URL,
        json={
            "action": "keysend",
            "params": params,
            "confirm": {
                "hotp": "x",
                "plan_hash": lc.plan_hash("keysend", params),
                "idempotency_key": "reconcile-down",
            },
        },
    )
    assert response.status_code == 403
    assert "ListPayments" in response.json()["detail"]


def test_execute_auto_within_envelope_is_inert(monkeypatch) -> None:
    env = PolicyEnvelope(
        allowed_actions=frozenset({"keysend"}), per_action_cap_sat=10_000, daily_cap_sat=50_000
    )
    _patch(monkeypatch, env)
    params = {"dest_pubkey_hex": "02ab", "amt_sat": 1000}
    r = TestClient(_app()).post(
        _URL,
        json={
            "action": "keysend",
            "params": params,
            "confirm": {
                "hotp": "x",
                "plan_hash": lc.plan_hash("keysend", params),
                "idempotency_key": "k",
            },
        },
    )
    assert r.status_code == 200
    assert r.json()["mode"] == "execute" and r.json()["result"]["state"] == "disabled"


def test_execute_auto_wrong_plan_hash_rejected(monkeypatch) -> None:
    env = PolicyEnvelope(
        allowed_actions=frozenset({"keysend"}), per_action_cap_sat=10_000, daily_cap_sat=50_000
    )
    _patch(monkeypatch, env)
    r = TestClient(_app()).post(
        _URL,
        json={
            "action": "keysend",
            "params": {"dest_pubkey_hex": "02ab", "amt_sat": 1000},
            "confirm": {"hotp": "x", "plan_hash": "WRONG", "idempotency_key": "wrong"},
        },
    )
    assert r.status_code == 403 and "confirm rejected" in r.json()["detail"]


def test_execute_auto_replayed_idempotency_key_rejected(monkeypatch) -> None:
    """W0-P4-Gate: ein Replay derselben create_invoice-Anfrage schlägt fehl."""
    env = PolicyEnvelope(allowed_actions=frozenset({"create_invoice"}))
    _patch(monkeypatch, env)
    client = TestClient(_app())
    params = {"memo": "w0p4", "value_sat": 0}
    plan = client.post(_URL, json={"action": "create_invoice", "params": params}).json()
    body = {
        "action": "create_invoice",
        "params": params,
        "confirm": {"hotp": "x", "plan_hash": plan["plan_hash"], "idempotency_key": "k-once"},
    }
    first = client.post(_URL, json=body)
    assert first.status_code == 200 and first.json()["mode"] == "execute"
    replay = client.post(_URL, json=body)
    assert replay.status_code == 403 and "replay" in replay.json()["detail"]


# --- W0-P1: receive actions do not require a capital-balance observation -----------


def test_receive_plan_does_not_poll_fresh_balance(monkeypatch) -> None:
    env = PolicyEnvelope(allowed_actions=frozenset({"create_invoice"}))
    _patch(monkeypatch, env)

    async def unexpected_poll() -> LightningBalanceSnapshot:
        raise AssertionError("receive action must not poll capital balance")

    monkeypatch.setattr(lc, "get_fresh_available_balance", unexpected_poll)
    response = TestClient(_app()).post(
        _URL, json={"action": "create_invoice", "params": {"memo": "w0p1", "value_sat": 0}}
    )
    assert response.status_code == 200
    assert response.json()["policy"]["decision"] == "auto_execute"


# --- pay_invoice amount is parsed from the BOLT11 (Audit-P0 completion) ------------
# A 25 000-sat invoice: HRP "lnbc250u1" → 250 * 100_000 msat = 25 000 sat.
_INV_25K = "lnbc250u1pjfaketestinvoicexxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
_INV_AMOUNTLESS = "lnbc1pjfaketestinvoicexxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"


def test_pay_invoice_amount_over_cap_needs_confirm(monkeypatch) -> None:
    # Without amount-parsing the policy saw 0 → auto_execute (covert spend hole).
    # Now the 25k invoice exceeds the 10k per-action cap → needs_confirm (HOTP).
    env = PolicyEnvelope(
        allowed_actions=frozenset({"pay_invoice"}),
        per_action_cap_sat=10_000,
        daily_cap_sat=1_000_000,
    )
    _patch(monkeypatch, env)
    r = TestClient(_app()).post(
        _URL, json={"action": "pay_invoice", "params": {"payment_request": _INV_25K}}
    )
    assert r.status_code == 200
    assert r.json()["policy"]["decision"] == "needs_confirm"


def test_pay_invoice_breaches_reserve_floor_is_denied(monkeypatch) -> None:
    # The reserve-floor backstop now applies to pay_invoice: balance 1_000_000 −
    # 25_000 = 975_000 < 990_000 floor → hard denied (was silently bypassed at amount=0).
    env = PolicyEnvelope(
        allowed_actions=frozenset({"pay_invoice"}),
        per_action_cap_sat=1_000_000,
        daily_cap_sat=1_000_000,
        reserve_floor_sat=990_000,
    )
    _patch(monkeypatch, env)
    r = TestClient(_app()).post(
        _URL, json={"action": "pay_invoice", "params": {"payment_request": _INV_25K}}
    )
    assert r.status_code == 200
    assert r.json()["policy"]["decision"] == "denied"


def test_pay_invoice_amountless_forces_confirm(monkeypatch) -> None:
    # Amountless invoice → amount unknown → fail-closed to needs_confirm even under
    # generous caps (never auto-execute an unbounded spend).
    env = PolicyEnvelope(
        allowed_actions=frozenset({"pay_invoice"}),
        per_action_cap_sat=1_000_000,
        daily_cap_sat=1_000_000,
    )
    _patch(monkeypatch, env)
    r = TestClient(_app()).post(
        _URL, json={"action": "pay_invoice", "params": {"payment_request": _INV_AMOUNTLESS}}
    )
    assert r.status_code == 200
    b = r.json()
    assert b["policy"]["decision"] == "needs_confirm"
    # W0-P4: die Kapitalklassen-Regel (amount<=0 → confirm) greift bereits in der
    # Policy; der Endpoint-Fallback "amount unknown" bleibt als Defense-in-Depth.
    assert "amount" in b["policy"]["reason"]


def test_execute_needs_confirm_rejects_bad_plan_hash(monkeypatch) -> None:
    # cap below the amount → needs_confirm; a wrong plan_hash is rejected before HOTP.
    env = PolicyEnvelope(
        allowed_actions=frozenset({"send_coins"}), per_action_cap_sat=100, daily_cap_sat=50_000
    )
    _patch(monkeypatch, env)
    r = TestClient(_app()).post(
        _URL,
        json={
            "action": "send_coins",
            "params": {"addr": "bc1q", "amount_sat": 1000},
            "confirm": {"hotp": "x", "plan_hash": "WRONG", "idempotency_key": "k"},
        },
    )
    assert r.status_code == 403 and "confirm rejected" in r.json()["detail"]


def test_auto_execute_still_requires_plan_hash_and_idempotency(monkeypatch) -> None:
    env = PolicyEnvelope(
        allowed_actions=frozenset({"create_invoice"}),
        per_action_cap_sat=10_000,
        daily_cap_sat=50_000,
    )
    _patch(monkeypatch, env)
    params = {"value_sat": 1000, "memo": "private"}
    client = TestClient(_app())
    bad = client.post(
        _URL,
        json={
            "action": "create_invoice",
            "params": params,
            "confirm": {"hotp": "", "plan_hash": "wrong", "idempotency_key": "mint-1"},
        },
    )
    assert bad.status_code == 403 and "plan hash" in bad.json()["detail"]

    body = {
        "action": "create_invoice",
        "params": params,
        "confirm": {
            "hotp": "",
            "plan_hash": lc.plan_hash("create_invoice", params),
            "idempotency_key": "mint-1",
        },
    }
    first = client.post(_URL, json=body)
    replay = client.post(_URL, json=body)
    assert first.status_code == 200
    assert replay.status_code == 403 and "replay" in replay.json()["detail"]


def test_stale_or_unavailable_balance_blocks_capital_action(monkeypatch) -> None:
    env = PolicyEnvelope(
        allowed_actions=frozenset({"pay_invoice"}),
        per_action_cap_sat=1_000_000,
        daily_cap_sat=1_000_000,
    )
    _patch(monkeypatch, env)

    async def _down() -> LightningBalanceSnapshot:
        return LightningBalanceSnapshot(state="unavailable", reason="node timeout")

    monkeypatch.setattr(lc, "get_fresh_available_balance", _down)
    r = TestClient(_app()).post(
        _URL, json={"action": "pay_invoice", "params": {"payment_request": _INV_25K}}
    )
    assert r.status_code == 200
    assert r.json()["policy"]["decision"] == "denied"
    assert "fresh node balance unavailable" in r.json()["policy"]["reason"]


def test_channel_close_class_never_auto_and_force_close_is_denied(monkeypatch) -> None:
    env = PolicyEnvelope(
        allowed_actions=frozenset({"close_channel"}),
        per_action_cap_sat=1_000_000,
        daily_cap_sat=1_000_000,
    )
    _patch(monkeypatch, env)
    client = TestClient(_app())
    cooperative = client.post(
        _URL,
        json={
            "action": "close_channel",
            "params": {"funding_txid": "aa", "output_index": 0, "force": False},
        },
    )
    assert cooperative.json()["policy"]["decision"] == "needs_confirm"
    assert "manual-only" in cooperative.json()["policy"]["reason"]

    forced = client.post(
        _URL,
        json={
            "action": "close_channel",
            "params": {"funding_txid": "aa", "output_index": 0, "force": True},
        },
    )
    assert forced.json()["policy"]["decision"] == "denied"
    assert "force close" in forced.json()["policy"]["reason"]


def test_caller_cannot_override_gate_owned_params(monkeypatch) -> None:
    _patch(monkeypatch, PolicyEnvelope.default())
    r = TestClient(_app()).post(
        _URL,
        json={
            "action": "pay_invoice",
            "params": {"payment_request": _INV_25K, "dry_run": False},
        },
    )
    assert r.status_code == 422 and "reserved params" in r.json()["detail"]
