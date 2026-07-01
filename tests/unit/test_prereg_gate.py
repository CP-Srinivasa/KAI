"""Unit tests for machine-checkable pre-registration gates."""

from __future__ import annotations

import pytest

from app.research.prereg_gate import check_gate, validate_gate

_EVAL = {
    "cost_bps": 30.0,
    "overall": {
        "n": 500,
        "horizons": {
            "86400": {
                "n": 480,
                "mean_bps": 40.0,
                "p_positive": 0.99,
                "top_symbol_share": 0.3,
                "cost_ref_bps": 33.0,
            }
        },
    },
    "stories": {
        "n": 200,
        "horizons": {
            "86400": {
                "n": 190,
                "mean_bps": 10.0,
                "p_positive": 0.80,
                "top_symbol_share": 0.4,
                "cost_ref_bps": 33.0,
            }
        },
    },
    "pooled": {
        "86400": {
            "k_sources": 12,
            "n_total": 470,
            "pooled_mean_bps": 20.0,
            "pooled_se_bps": 7.0,
            "z": 2.85,
            "p_positive_normal": 0.998,
            "i_squared": 0.03,
        }
    },
}


def _gate(**kw) -> dict:
    base = {"level": "overall", "horizon_s": 86400, "n_min": 300, "p_min": 0.95}
    base.update(kw)
    return base


def test_validate_gate_rejects_malformed() -> None:
    with pytest.raises(ValueError, match="missing required key"):
        validate_gate({"level": "overall"})
    with pytest.raises(ValueError, match="level must be"):
        validate_gate(_gate(level="sideways"))
    with pytest.raises(ValueError, match="p_min"):
        validate_gate(_gate(p_min=1.5))
    with pytest.raises(ValueError, match="horizon_s"):
        validate_gate(_gate(horizon_s=-1))
    validate_gate(_gate())  # well-formed passes


def test_overall_gate_passes_and_cost_bar_applies() -> None:
    import copy

    res = check_gate(_gate(require_cost_clearing=True), _EVAL)
    assert res["passed"] is True
    assert "PASSED" in res["verdict"]
    # raise the bar above the measured mean -> cost check fails mechanically
    strict = copy.deepcopy(_EVAL)
    strict["overall"]["horizons"]["86400"]["cost_ref_bps"] = 45.0
    res2 = check_gate(_gate(require_cost_clearing=True), strict)
    assert res2["passed"] is False
    assert "cost_clearing" in res2["verdict"]


def test_stories_level_fails_on_p() -> None:
    res = check_gate(_gate(level="stories", n_min=100), _EVAL)
    assert res["passed"] is False
    assert any(c["name"] == "p_min" and not c["ok"] for c in res["checks"])


def test_pooled_level_checks_k_and_i2_and_uses_base_cost() -> None:
    gate = _gate(level="pooled", n_min=300, i2_max=0.5, k_min=8, require_cost_clearing=True)
    res = check_gate(gate, _EVAL)
    # pooled mean 20.0 < base cost 30.0 -> cost check fails; k/i2/p/n pass
    names_ok = {c["name"]: c["ok"] for c in res["checks"]}
    assert names_ok["k_min"] and names_ok["i2_max"] and names_ok["p_min"]
    assert not names_ok["cost_clearing"]
    assert res["passed"] is False


def test_missing_block_is_fail_closed_not_raise() -> None:
    res = check_gate(_gate(level="pooled", horizon_s=999), _EVAL)
    assert res["passed"] is False
    assert res["checks"][0]["ok"] is False  # "present" check


def test_int_horizon_keys_also_resolve() -> None:
    ev = {"overall": {"n": 500, "horizons": {86400: _EVAL["overall"]["horizons"]["86400"]}}}
    res = check_gate(_gate(), ev)
    assert res["passed"] is True


def test_top_symbol_share_bar() -> None:
    res = check_gate(_gate(max_top_symbol_share=0.2), _EVAL)
    assert res["passed"] is False
    assert any(c["name"] == "max_top_symbol_share" and not c["ok"] for c in res["checks"])
