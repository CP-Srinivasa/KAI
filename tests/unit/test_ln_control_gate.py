"""B-005 — capital-confirm gate for irreversible value-layer POSTs (security core).

An irreversible execute requires ALL of: a matching plan-hash (no plan substitution
between preview and execute), a fresh idempotency key (no replay), and a valid fresh
HOTP (out-of-band 2nd factor, replay-safe). Cheap checks run first so a bad plan
NEVER advances the HOTP counter.
"""

from __future__ import annotations

from app.lightning.control_gate import plan_hash, verify_capital_confirm


class _FakeHotp:
    """Stub HotpVerifier: accepts 'good', raises otherwise; records calls."""

    def __init__(self) -> None:
        self.calls = 0

    def verify(self, code: str):  # noqa: ANN201
        self.calls += 1
        if code != "good":
            raise RuntimeError("hotp verification failed")
        return True


def test_plan_hash_is_deterministic_and_param_order_independent() -> None:
    a = plan_hash("pay_invoice", {"amount_sat": 1000, "dest": "x"})
    b = plan_hash("pay_invoice", {"dest": "x", "amount_sat": 1000})
    assert a == b and len(a) == 64


def test_plan_hash_changes_with_params() -> None:
    assert plan_hash("pay_invoice", {"amount_sat": 1000}) != plan_hash(
        "pay_invoice", {"amount_sat": 2000}
    )


def test_confirm_ok_with_match_fresh_key_and_hotp() -> None:
    seen: set[str] = set()
    hotp = _FakeHotp()
    h = plan_hash("pay_invoice", {"amount_sat": 1000})
    v = verify_capital_confirm(
        hotp_verifier=hotp,
        hotp_code="good",
        submitted_plan_hash=h,
        expected_plan_hash=h,
        idempotency_key="k1",
        seen_keys=seen,
    )
    assert v.ok and "k1" in seen and hotp.calls == 1


def test_plan_hash_mismatch_rejected_without_touching_hotp() -> None:
    seen: set[str] = set()
    hotp = _FakeHotp()
    v = verify_capital_confirm(
        hotp_verifier=hotp,
        hotp_code="good",
        submitted_plan_hash="deadbeef",
        expected_plan_hash="abc123",
        idempotency_key="k1",
        seen_keys=seen,
    )
    assert not v.ok and "plan hash" in v.reason
    assert hotp.calls == 0  # bad plan must NOT advance the HOTP counter
    assert "k1" not in seen


def test_idempotency_replay_rejected() -> None:
    seen = {"used"}
    hotp = _FakeHotp()
    v = verify_capital_confirm(
        hotp_verifier=hotp,
        hotp_code="good",
        submitted_plan_hash="h",
        expected_plan_hash="h",
        idempotency_key="used",
        seen_keys=seen,
    )
    assert not v.ok and "replay" in v.reason
    assert hotp.calls == 0


def test_bad_hotp_rejected_and_key_not_consumed() -> None:
    seen: set[str] = set()
    hotp = _FakeHotp()
    v = verify_capital_confirm(
        hotp_verifier=hotp,
        hotp_code="wrong",
        submitted_plan_hash="h",
        expected_plan_hash="h",
        idempotency_key="k1",
        seen_keys=seen,
    )
    assert not v.ok and "hotp" in v.reason
    assert "k1" not in seen  # failed confirm does not burn the idempotency key
