"""Sprint D (2026-06-01): edge release policy — promotion decision engine.

Behaviour under test (kai-testing-regeln — behaviour, not implementation):

- P/net thresholds map to the Goal ladder at every boundary
  (0.0 / 0.49 / 0.50 / 0.79 / 0.80 / 0.94 / 0.95 / 0.99).
- insufficient posterior (P=None) OR n < min_n -> DISABLED (no defensible
  posterior may auto-release live).
- positive P but net <= safety_margin -> capped at PAPER (no real edge).
- any live_* recommendation sets requires_operator_signoff.
- the engine NEVER auto-promotes to LIVE_NORMAL (explicit dedicated test).
- oos_stable=False blocks LIVE_NORMAL (downgraded to LIVE_LIMITED).
- assess_oos_stability counts only disjoint days that clear BOTH P and net.
- the decision object is JSON-serialisable and surfaces current vs recommended.
- the real 2026-06-01 cohort shape (P=0.00, net negative) -> DISABLED.
"""

from __future__ import annotations

from app.core.enums import EntryMode
from app.observability.edge_report import CohortEdge
from app.risk.edge_release_policy import (
    DEFAULT_MIN_N,
    ReleaseDecision,
    assess_oos_stability,
    decide_release,
    render_decision,
)

# --- builders ------------------------------------------------------------------


def _cohort(
    *,
    p: float | None,
    net: float,
    count: int = 50,
    key: str = "ALL",
    cohort_type: str = "overall",
) -> CohortEdge:
    """Synthetic CohortEdge with a controlled posterior P and net edge.

    Only the fields the policy reads (p_mu_net_positive, net_bps_per_notional_mean,
    count, cohort_key, cohort_type) need to be meaningful; the rest are filler.
    """
    return CohortEdge(
        cohort_key=key,
        cohort_type=cohort_type,
        count=count,
        gross_bps_sum=0.0,
        gross_bps_mean=0.0,
        fee_bps_mean=0.0,
        spread_bps_mean=0.0,
        slippage_bps_mean=0.0,
        net_bps_sum=net * count,
        net_bps_mean=net,
        net_bps_per_notional_mean=net,
        winrate=0.0,
        avg_win_bps=0.0,
        avg_loss_bps=0.0,
        p_mu_net_positive=p,
        realized_pnl_usd_sum=0.0,
    )


# --- insufficient posterior -> DISABLED ----------------------------------------


def test_p_none_is_disabled():
    d = decide_release(_cohort(p=None, net=50.0, count=100))
    assert d.recommended_mode is EntryMode.DISABLED
    assert d.requires_operator_signoff is False
    assert "insufficient" in d.reasoning.lower()


def test_count_below_min_n_is_disabled_even_with_high_p():
    # High P, strong net, but only 5 trades: not defensible -> DISABLED.
    d = decide_release(_cohort(p=0.99, net=200.0, count=5), min_n=DEFAULT_MIN_N)
    assert d.recommended_mode is EntryMode.DISABLED
    assert "min_n" in d.reasoning


def test_count_exactly_min_n_is_allowed():
    d = decide_release(_cohort(p=0.99, net=50.0, count=DEFAULT_MIN_N), oos_stable=True)
    assert d.recommended_mode is not EntryMode.DISABLED


# --- P ladder boundaries (net comfortably positive) ----------------------------


def test_p_zero_is_disabled():
    assert decide_release(_cohort(p=0.0, net=50.0)).recommended_mode is EntryMode.DISABLED


def test_p_049_is_disabled():
    assert decide_release(_cohort(p=0.49, net=50.0)).recommended_mode is EntryMode.DISABLED


def test_p_050_is_paper():
    assert decide_release(_cohort(p=0.50, net=50.0)).recommended_mode is EntryMode.PAPER


def test_p_079_is_paper():
    assert decide_release(_cohort(p=0.79, net=50.0)).recommended_mode is EntryMode.PAPER


def test_p_080_is_live_limited():
    d = decide_release(_cohort(p=0.80, net=50.0))
    assert d.recommended_mode is EntryMode.LIVE_LIMITED
    assert d.requires_operator_signoff is True


def test_p_094_is_live_limited():
    d = decide_release(_cohort(p=0.94, net=50.0))
    assert d.recommended_mode is EntryMode.LIVE_LIMITED
    assert d.requires_operator_signoff is True


def test_p_095_oos_stable_is_live_normal_eligible():
    d = decide_release(_cohort(p=0.95, net=50.0), oos_stable=True)
    assert d.recommended_mode is EntryMode.LIVE_NORMAL
    assert d.requires_operator_signoff is True
    assert "eligible" in d.reasoning.lower()


def test_p_099_oos_stable_is_live_normal_eligible():
    d = decide_release(_cohort(p=0.99, net=50.0), oos_stable=True)
    assert d.recommended_mode is EntryMode.LIVE_NORMAL
    assert d.requires_operator_signoff is True


# --- net edge cap: positive P but no real edge ---------------------------------


def test_high_p_but_net_below_margin_is_capped_at_paper():
    # P would otherwise reach LIVE_NORMAL, but net <= margin: no real edge.
    d = decide_release(_cohort(p=0.99, net=0.0), safety_margin_bps=0.0, oos_stable=True)
    assert d.recommended_mode is EntryMode.PAPER
    assert "no real cost-adjusted edge" in d.reasoning


def test_net_must_strictly_exceed_margin():
    # net exactly == margin is NOT enough (strict >).
    d = decide_release(_cohort(p=0.99, net=5.0), safety_margin_bps=5.0, oos_stable=True)
    assert d.recommended_mode is EntryMode.PAPER


def test_net_just_above_margin_unlocks_live():
    d = decide_release(_cohort(p=0.85, net=5.01), safety_margin_bps=5.0)
    assert d.recommended_mode is EntryMode.LIVE_LIMITED


def test_negative_net_with_high_p_is_capped_at_paper():
    d = decide_release(_cohort(p=0.99, net=-10.0), oos_stable=True)
    assert d.recommended_mode is EntryMode.PAPER


# --- never auto LIVE_NORMAL / oos blocks ---------------------------------------


def test_never_auto_live_normal_without_oos():
    # P >= 0.95, strong net, but OOS not established -> downgraded to LIVE_LIMITED.
    d = decide_release(_cohort(p=0.99, net=100.0), oos_stable=False)
    assert d.recommended_mode is EntryMode.LIVE_LIMITED
    assert d.recommended_mode is not EntryMode.LIVE_NORMAL
    assert "out-of-sample" in d.reasoning.lower()


def test_live_normal_always_requires_signoff_never_auto():
    # Even the strongest possible evidence returns "eligible + signoff", not an
    # auto-promote. The engine has no code path that emits an actionable promote.
    d = decide_release(_cohort(p=1.0, net=1000.0, count=10_000), oos_stable=True)
    assert d.recommended_mode is EntryMode.LIVE_NORMAL
    assert d.requires_operator_signoff is True
    assert "never auto" in d.reasoning.lower()


def test_no_live_mode_without_signoff_flag():
    # Property: every live recommendation carries the sign-off flag, every
    # non-live one does not.
    for p, net, oos in [(0.0, 50, False), (0.6, 50, False), (0.85, 50, False), (0.99, 50, True)]:
        d = decide_release(_cohort(p=p, net=net), oos_stable=oos)
        assert d.requires_operator_signoff == d.recommended_mode.is_live


# --- OOS stability assessment --------------------------------------------------


def test_oos_needs_min_disjoint_qualifying_days():
    # Two days clear both P>=0.95 and net>0; one fails P. Need >=2 -> stable.
    days = [
        _cohort(p=0.97, net=30.0, count=30, key="2026-06-01", cohort_type="day"),
        _cohort(p=0.96, net=20.0, count=25, key="2026-06-02", cohort_type="day"),
        _cohort(p=0.40, net=10.0, count=15, key="2026-06-03", cohort_type="day"),
    ]
    stable, breakdown = assess_oos_stability(days, min_disjoint_days=2)
    assert stable is True
    assert sum(1 for b in breakdown if b["qualifies"]) == 2


def test_oos_one_qualifying_day_is_not_stable():
    days = [
        _cohort(p=0.97, net=30.0, count=30, key="2026-06-01", cohort_type="day"),
        _cohort(p=0.50, net=30.0, count=30, key="2026-06-02", cohort_type="day"),
    ]
    stable, _ = assess_oos_stability(days, min_disjoint_days=2)
    assert stable is False


def test_oos_day_with_positive_p_but_negative_net_does_not_qualify():
    days = [
        _cohort(p=0.99, net=-5.0, count=30, key="2026-06-01", cohort_type="day"),
        _cohort(p=0.99, net=-5.0, count=30, key="2026-06-02", cohort_type="day"),
    ]
    stable, breakdown = assess_oos_stability(days, min_disjoint_days=2)
    assert stable is False
    assert all(b["qualifies"] is False for b in breakdown)


def test_oos_insufficient_day_never_counts():
    days = [
        _cohort(p=None, net=50.0, count=3, key="2026-06-01", cohort_type="day"),
        _cohort(p=0.99, net=50.0, count=30, key="2026-06-02", cohort_type="day"),
    ]
    stable, _ = assess_oos_stability(days, min_disjoint_days=2)
    assert stable is False


def test_oos_empty_days_is_not_stable():
    stable, breakdown = assess_oos_stability([], min_disjoint_days=2)
    assert stable is False
    assert breakdown == []


# --- decision object contract --------------------------------------------------


def test_decision_is_json_serialisable():
    import json

    d = decide_release(
        _cohort(p=0.99, net=50.0),
        current_mode=EntryMode.PAPER,
        oos_stable=True,
    )
    payload = json.loads(json.dumps(d.to_dict()))
    assert payload["recommended_mode"] == "live_normal"
    assert payload["current_mode"] == "paper"
    assert payload["requires_operator_signoff"] is True
    assert payload["recommends_change"] is True


def test_recommends_change_false_when_equal():
    d = decide_release(_cohort(p=0.0, net=50.0), current_mode=EntryMode.DISABLED)
    assert d.recommended_mode is EntryMode.DISABLED
    assert d.recommends_change is False


def test_current_mode_none_recommends_change_false():
    d = decide_release(_cohort(p=0.0, net=50.0), current_mode=None)
    assert d.recommends_change is False


# --- the real 2026-06-01 cohort -------------------------------------------------


def test_real_20260601_cohort_is_disabled():
    """Reproduces the confirmed Pi shape: P(mu_net>0)=0.00, net ~ -69 bps.

    This is the load-bearing acceptance check: with a 0% posterior and a
    negative cost-adjusted edge, the only safe recommendation is DISABLED — and
    it must carry NO operator-signoff (it is not a live recommendation at all).
    """
    real = _cohort(p=0.00, net=-69.0, count=22, key="2026-06-01", cohort_type="day")
    d = decide_release(real, current_mode=EntryMode.PAPER, min_n=DEFAULT_MIN_N)
    assert d.recommended_mode is EntryMode.DISABLED
    assert d.requires_operator_signoff is False
    # n=22 >= min_n=20, so the disable reason is the posterior, not sample size.
    assert "< 50%" in d.reasoning or "insufficient" in d.reasoning.lower()


def test_render_decision_is_human_readable():
    d = decide_release(_cohort(p=0.00, net=-69.0, count=22), current_mode=EntryMode.PAPER)
    text = render_decision(d)
    assert "EDGE RELEASE DECISION" in text
    assert "DISABLED" in text
    assert "RECOMMENDED" in text


def test_returns_release_decision_type():
    assert isinstance(decide_release(_cohort(p=0.0, net=0.0)), ReleaseDecision)
