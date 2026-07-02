"""Tests for the V5 funding/OI evidence evaluator core.

Since 2026-07-01 the mechanics live in reusable modules (:mod:`app.research.
shadow_outcomes` + :mod:`app.research.shadow_evidence_eval`); ``scripts/
evaluate_v5_evidence.py`` is a thin CLI over them. These pin the behaviour that
matters for an honest verdict: sentinel filtering, entry-time resolution (ledger +
tech-id fallback), the symbol+side nearest-evidence join with a tolerance, and the
conservative cost+significance+concentration gate. A learned direction stays a
hypothesis — these tests guard the gate, not a trading claim.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.research.shadow_evidence_eval import evaluate_signal, index_evidence, nearest_aligned
from app.research.shadow_outcomes import build_outcomes, entry_ts_for, load_entry_times

_T0 = datetime(2026, 6, 25, 12, 0, 0, tzinfo=UTC)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _resolved(cid: str, symbol: str, side: str, fwd: dict[int, float]) -> dict:
    rec = {"candidate_id": cid, "symbol": symbol, "side": side}
    for h, v in fwd.items():
        rec[f"fwd_{h}s_bps"] = v
    return rec


# --------------------------------------------------------------------------- #
# entry-time resolution
# --------------------------------------------------------------------------- #
def test_entry_ts_from_ledger() -> None:
    et = load_entry_times([{"candidate_id": "cyc_abc", "ts_utc": _iso(_T0)}])
    assert entry_ts_for({"candidate_id": "cyc_abc"}, et) == _T0


def test_entry_ts_tech_id_fallback() -> None:
    cid = f"tech-WLDUSDT-{_iso(_T0)}"
    assert entry_ts_for({"candidate_id": cid}, {}) == _T0


def test_entry_ts_unknown_cyc_id_is_none() -> None:
    # autonomous_generator cyc_* ids carry no time; without a ledger entry → None
    assert entry_ts_for({"candidate_id": "cyc_nope"}, {}) is None


# --------------------------------------------------------------------------- #
# sentinel filtering
# --------------------------------------------------------------------------- #
def test_build_outcomes_drops_sentinel() -> None:
    rows = [
        _resolved("tech-A-" + _iso(_T0), "A/USDT", "long", {60: 12.0, 300: 8.0}),
        _resolved("tech-B-" + _iso(_T0), "B/USDT", "long", {60: -9950.0}),  # delisted sentinel
    ]
    out = build_outcomes(rows, {}, max_abs_bps=5000.0)
    assert [o["symbol"] for o in out] == ["A/USDT"]


def test_build_outcomes_time_sorted() -> None:
    rows = [
        _resolved("tech-A-" + _iso(_T0 + timedelta(seconds=60)), "A/USDT", "long", {60: 1.0}),
        _resolved("tech-A-" + _iso(_T0), "A/USDT", "long", {60: 2.0}),
    ]
    out = build_outcomes(rows, {}, max_abs_bps=5000.0)
    assert [o["entry_ts"] for o in out] == sorted(o["entry_ts"] for o in out)


# --------------------------------------------------------------------------- #
# join
# --------------------------------------------------------------------------- #
def test_nearest_aligned_within_and_outside_tol() -> None:
    idx = index_evidence(
        [
            {
                "ts": _iso(_T0 + timedelta(seconds=30)),
                "symbol": "A/USDT",
                "direction": "long",
                "evidence_direction_aligned": 1,
            },
            {
                "ts": _iso(_T0 + timedelta(seconds=600)),
                "symbol": "A/USDT",
                "direction": "long",
                "evidence_direction_aligned": -1,
            },
        ]
    )
    o = {"symbol": "A/USDT", "side": "long", "entry_ts": _T0}
    assert nearest_aligned(o, idx, tol_s=300.0) == 1  # the 30s-away one
    # wrong symbol → no join
    assert (
        nearest_aligned({"symbol": "Z/USDT", "side": "long", "entry_ts": _T0}, idx, tol_s=300.0)
        is None
    )


# --------------------------------------------------------------------------- #
# gate
# --------------------------------------------------------------------------- #
def _planted(symbols: list[str], aligned: int, fwd60: float) -> tuple[list[dict], list[dict]]:
    resolved, evidence = [], []
    for i in range(12):
        sym = symbols[i % len(symbols)]
        t = _T0 + timedelta(seconds=120 * i)
        resolved.append(_resolved(f"tech-{sym}-{_iso(t)}", sym, "long", {60: fwd60}))
        evidence.append(
            {
                "ts": _iso(t + timedelta(seconds=10)),
                "symbol": sym,
                "direction": "long",
                "evidence_direction_aligned": aligned,
            }
        )
    return resolved, evidence


def test_actionable_when_aligned_cohort_clears_cost_diverse() -> None:
    resolved, evidence = _planted(["A/USDT", "B/USDT", "C/USDT"], aligned=1, fwd60=55.0)
    out = build_outcomes(resolved, {}, max_abs_bps=5000.0)
    res = evaluate_signal(
        out, index_evidence(evidence), tol_s=300.0, cost_bps=20.0, max_concentration=0.8
    )
    assert res["horizons"][60]["actionable"] is True
    assert res["actionable"] is True


def test_noise_cohort_stays_shadow_only() -> None:
    # aligned+1 mean ~0 (alternating ±2bps) → below cost, not significant
    resolved, evidence = [], []
    for i in range(12):
        sym = ["A/USDT", "B/USDT"][i % 2]
        t = _T0 + timedelta(seconds=120 * i)
        resolved.append(
            _resolved(f"tech-{sym}-{_iso(t)}", sym, "long", {60: 2.0 if i % 2 == 0 else -2.0})
        )
        evidence.append(
            {
                "ts": _iso(t + timedelta(seconds=10)),
                "symbol": sym,
                "direction": "long",
                "evidence_direction_aligned": 1,
            }
        )
    out = build_outcomes(resolved, {}, max_abs_bps=5000.0)
    res = evaluate_signal(
        out, index_evidence(evidence), tol_s=300.0, cost_bps=20.0, max_concentration=0.8
    )
    assert res["horizons"][60]["actionable"] is False
    assert "SHADOW_ONLY" in res["verdict"]


def test_concentration_guard_blocks_single_symbol() -> None:
    # strong +55bps but ALL one symbol → top_symbol_share=1.0 > 0.8 → not actionable
    resolved, evidence = _planted(["A/USDT"], aligned=1, fwd60=55.0)
    out = build_outcomes(resolved, {}, max_abs_bps=5000.0)
    res = evaluate_signal(
        out, index_evidence(evidence), tol_s=300.0, cost_bps=20.0, max_concentration=0.8
    )
    assert res["horizons"][60]["top_symbol_share"] == 1.0
    assert res["horizons"][60]["actionable"] is False
