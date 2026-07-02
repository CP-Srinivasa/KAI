"""S-002 — mint-rate-limit for the L402 receive side (security blocker).

dry_run-default does NOT protect the receive side: ``_issue_challenge`` mints a REAL
invoice on every unpaid request, so an unauth flood = DoS/HTLC-flood. The limiter
caps mints per key (ip:scope) AND globally per window, BEFORE L402 may be enabled.
Deterministic via an injected clock.
"""

from __future__ import annotations

from app.lightning.mint_limiter import MintLimiter


def test_per_key_cap_blocks_after_max() -> None:
    lim = MintLimiter(per_key_max=2, global_max=100, window_s=60.0)
    assert lim.allow("ip1:scope", now=0.0) is True
    assert lim.allow("ip1:scope", now=1.0) is True
    assert lim.allow("ip1:scope", now=2.0) is False  # 3rd in window → blocked


def test_other_key_unaffected_by_first_keys_cap() -> None:
    lim = MintLimiter(per_key_max=1, global_max=100, window_s=60.0)
    assert lim.allow("ip1", now=0.0) is True
    assert lim.allow("ip1", now=1.0) is False
    assert lim.allow("ip2", now=1.0) is True  # independent key


def test_global_budget_blocks_across_keys() -> None:
    lim = MintLimiter(per_key_max=100, global_max=2, window_s=60.0)
    assert lim.allow("a", now=0.0) is True
    assert lim.allow("b", now=0.0) is True
    assert lim.allow("c", now=0.0) is False  # global budget exhausted


def test_window_resets_after_elapse() -> None:
    lim = MintLimiter(per_key_max=1, global_max=1, window_s=60.0)
    assert lim.allow("a", now=0.0) is True
    assert lim.allow("a", now=30.0) is False  # same window
    assert lim.allow("a", now=61.0) is True  # new window → reset


def test_disabled_caps_allow_everything() -> None:
    # per_key_max<=0 or global_max<=0 → limiter is a no-op (caps unconfigured)
    lim = MintLimiter(per_key_max=0, global_max=0, window_s=60.0)
    for i in range(50):
        assert lim.allow("a", now=float(i)) is True
