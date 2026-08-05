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
from app.lightning.policy import PolicyEnvelope

_URL = "/dashboard/api/ln/value-action"


def _app() -> FastAPI:
    a = FastAPI()
    a.include_router(lc.router)
    return a


async def _bal_million() -> int:
    return 1_000_000


async def _fresh_bal_million() -> int | None:
    return 1_000_000


async def _fresh_bal_none() -> int | None:
    return None


def _patch(monkeypatch, envelope: PolicyEnvelope) -> None:
    lc.reset_control_state()
    monkeypatch.setattr(lc.PolicyStore, "load", lambda self: envelope)
    monkeypatch.setattr(lc, "_available_balance_sat", _bal_million)
    # Capital actions read the W0-P1 freshness-gated balance; default the tests to
    # "fresh and rich" so policy decisions stay deterministic.
    monkeypatch.setattr(lc, "_fresh_capital_balance_sat", _fresh_bal_million)
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


def test_execute_auto_within_envelope_is_inert(monkeypatch) -> None:
    env = PolicyEnvelope(
        allowed_actions=frozenset({"send_coins"}), per_action_cap_sat=10_000, daily_cap_sat=50_000
    )
    _patch(monkeypatch, env)
    client = TestClient(_app())
    params = {"addr": "bc1q", "amount_sat": 1000}
    plan = client.post(_URL, json={"action": "send_coins", "params": params}).json()
    r = client.post(
        _URL,
        json={
            "action": "send_coins",
            "params": params,
            "confirm": {
                "hotp": "x",
                "plan_hash": plan["plan_hash"],
                "idempotency_key": "k-auto-1",
            },
        },
    )
    assert r.status_code == 200
    b = r.json()
    # auto_execute needs NO HOTP (max automation) but MUST echo the previewed
    # plan_hash and burn a fresh idempotency key (W0-P4); stays INERT (pay off).
    assert b["mode"] == "execute" and b["result"]["state"] == "disabled"


# --- W0-P4: the auto_execute path enforces plan binding + replay guard -------------


def test_execute_auto_wrong_plan_hash_rejected(monkeypatch) -> None:
    """Previously the auto path ignored the confirm content entirely — params could
    be substituted between preview and execute without detection."""
    env = PolicyEnvelope(
        allowed_actions=frozenset({"send_coins"}), per_action_cap_sat=10_000, daily_cap_sat=50_000
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


# --- W0-P1: capital actions fail closed on stale/unavailable node state ------------


def test_capital_action_stale_node_state_is_denied(monkeypatch) -> None:
    """W0-P1-Gate: ohne frischen, balance-tragenden Node-State wird eine
    Kapitalaktion hart abgelehnt — nie gegen einen stale Cache-Stand bewertet."""
    env = PolicyEnvelope(
        allowed_actions=frozenset({"send_coins"}),
        per_action_cap_sat=1_000_000,
        daily_cap_sat=1_000_000,
    )
    _patch(monkeypatch, env)
    monkeypatch.setattr(lc, "_fresh_capital_balance_sat", _fresh_bal_none)
    client = TestClient(_app())
    params = {"addr": "bc1q", "amount_sat": 1000}
    plan = client.post(_URL, json={"action": "send_coins", "params": params})
    assert plan.status_code == 200
    assert plan.json()["policy"]["decision"] == "denied"
    assert "stale" in plan.json()["policy"]["reason"]
    r = client.post(
        _URL,
        json={
            "action": "send_coins",
            "params": params,
            "confirm": {"hotp": "x", "plan_hash": plan.json()["plan_hash"], "idempotency_key": "k"},
        },
    )
    assert r.status_code == 403 and "policy denied" in r.json()["detail"]


def test_stale_node_state_does_not_block_receive_action(monkeypatch) -> None:
    """create_invoice (receive, kein Kapitalabfluss) bleibt bei stale Node-State
    nutzbar — das Freshness-Gate bindet nur Kapitalaktionen."""
    env = PolicyEnvelope(allowed_actions=frozenset({"create_invoice"}))
    _patch(monkeypatch, env)
    monkeypatch.setattr(lc, "_fresh_capital_balance_sat", _fresh_bal_none)
    r = TestClient(_app()).post(
        _URL, json={"action": "create_invoice", "params": {"memo": "w0p1", "value_sat": 0}}
    )
    assert r.status_code == 200
    assert r.json()["policy"]["decision"] == "auto_execute"


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
