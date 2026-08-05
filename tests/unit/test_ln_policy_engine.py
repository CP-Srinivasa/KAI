"""Sprint 5 — Policy-Envelope-Engine (max-automation gatekeeper).

The operator sets limits ONCE; every capital-effective action is classified:
``auto_execute`` (inside envelope) | ``needs_confirm`` (out-of-policy / over
threshold / new counterparty) | ``denied`` (disallowed action or reserve-floor
breach). Safe default = deny everything until policies are configured.
"""

from __future__ import annotations

from app.lightning.policy import PolicyEnvelope, PolicyStore, evaluate_policy


def _env(**kw) -> PolicyEnvelope:
    base: dict = {
        "allowed_actions": frozenset({"pay_invoice"}),
        "per_action_cap_sat": 10_000,
        "daily_cap_sat": 50_000,
        "confirm_threshold_sat": 0,
        "recipient_allowlist": frozenset(),
        "reserve_floor_sat": 0,
    }
    base.update(kw)
    return PolicyEnvelope(**base)


def _eval(env, *, action="pay_invoice", amount=1000, recipient=None, spent=0, avail=1_000_000):
    return evaluate_policy(
        action,
        amount_sat=amount,
        recipient=recipient,
        spent_today_sat=spent,
        available_balance_sat=avail,
        envelope=env,
    )


def test_default_envelope_denies_everything() -> None:
    d = _eval(PolicyEnvelope.default(), action="pay_invoice", amount=1)
    assert d.decision == "denied"


def test_disallowed_action_denied() -> None:
    d = _eval(_env(), action="send_coins")
    assert d.decision == "denied" and "not allowed" in d.reason


def test_within_envelope_auto_executes() -> None:
    assert _eval(_env(), amount=1000, spent=0).decision == "auto_execute"


def test_over_per_action_cap_needs_confirm() -> None:
    assert _eval(_env(), amount=20_000).decision == "needs_confirm"


def test_over_daily_cap_needs_confirm() -> None:
    assert _eval(_env(), amount=10_000, spent=45_000).decision == "needs_confirm"


def test_new_counterparty_needs_confirm() -> None:
    env = _env(recipient_allowlist=frozenset({"02aa"}))
    assert _eval(env, recipient="02bb").decision == "needs_confirm"
    assert _eval(env, recipient="02aa").decision == "auto_execute"


def test_confirm_threshold_forces_confirm_even_within_caps() -> None:
    env = _env(confirm_threshold_sat=5000)
    assert _eval(env, amount=6000).decision == "needs_confirm"
    assert _eval(env, amount=4000).decision == "auto_execute"


def test_reserve_floor_breach_denied() -> None:
    env = _env(reserve_floor_sat=90_000)
    assert _eval(env, amount=20_000, avail=100_000).decision == "denied"  # 80k < 90k floor


# --- W0-P4: Risikoklassen pro Aktion (statt reiner Betragsschwelle) ---------------


def test_unclassified_action_denied_even_if_allowlisted() -> None:
    """Deny-by-default: eine der Policy unbekannte Aktionsklasse wird abgelehnt,
    selbst wenn die Allowlist sie (versehentlich) nennt."""
    env = _env(allowed_actions=frozenset({"transmogrify"}))
    d = _eval(env, action="transmogrify", amount=0)
    assert d.decision == "denied" and "unclassified" in d.reason


def test_close_channel_blocked_by_class_not_only_by_allowlist() -> None:
    """W0-P4-Gate: hypothetisch allowlistetes close_channel (amount=0) darf NIE
    auto-executen — die Klasse erzwingt Operator-Confirm, nicht nur die Allowlist."""
    env = _env(allowed_actions=frozenset({"close_channel"}))
    d = _eval(env, action="close_channel", amount=0)
    assert d.decision == "needs_confirm" and "irreversible" in d.reason


def test_zero_amount_capital_actions_never_auto_execute() -> None:
    """amount<=0 lief bisher an per-action-Cap, Daily-Cap UND Confirm-Threshold
    (>0-Guard) vorbei — Kapitalklassen fallen jetzt auf needs_confirm zurück."""
    for action in ("pay_invoice", "keysend", "send_coins", "open_channel"):
        env = _env(allowed_actions=frozenset({action}))
        d = _eval(env, action=action, amount=0)
        assert d.decision == "needs_confirm", action
        assert "zero/unknown amount" in d.reason


def test_receive_class_zero_amount_still_auto_executes() -> None:
    """create_invoice (receive-Klasse, kein Kapitalabfluss) bleibt max-automation."""
    env = _env(allowed_actions=frozenset({"create_invoice"}))
    assert _eval(env, action="create_invoice", amount=0).decision == "auto_execute"


# --- store ----------------------------------------------------------------------


def test_store_missing_loads_default_deny(tmp_path) -> None:
    env = PolicyStore(tmp_path / "policy.json").load()
    assert env.allowed_actions == frozenset()
    assert _eval(env, amount=1).decision == "denied"


def test_store_roundtrip(tmp_path) -> None:
    p = tmp_path / "policy.json"
    store = PolicyStore(p)
    store.save(
        _env(per_action_cap_sat=12345, allowed_actions=frozenset({"pay_invoice", "keysend"}))
    )
    env = store.load()
    assert env.per_action_cap_sat == 12345
    assert env.allowed_actions == frozenset({"pay_invoice", "keysend"})
