"""Ein negativer Lift ist noch kein Befund.

2026-09-09: Das Dashboard stufte `priority_tier_lift_pct` allein nach dem
Vorzeichen auf `critical` und meldete "High-P trifft AKTIV SCHLECHTER als
Standard-Prioritaet". Gemessen am selben Tag:

    High-Conviction (P10)   69,64 %   n=112   Wilson-CI [60,59 ; 77,39]
    Standard (P7-P9)        77,78 %   n= 54   Wilson-CI [65,06 ; 86,80]
    lift                    -8,14 pp

Die Konfidenzintervalle ueberlappen auf ganzer Breite — der Unterschied ist
Rauschen. Die CIs werden in hold_metrics.py bereits berechnet und lagen
ungenutzt im selben Dict. Genau dieselbe Disjunktheits-Pruefung ist im Projekt
schon Standard (TV-4 Quality-Bar, per-source precision).
"""

from __future__ import annotations

from app.observability.dashboard_precision_metrics import classify_priority_tier_lift


def _quality(*, high, standard, lift):
    hi_rate, hi_n, hi_lo, hi_hi = high
    st_rate, st_n, st_lo, st_hi = standard
    return {
        "priority_tier_lift_pct": lift,
        "priority_tier_high_conviction_hit_rate_pct": hi_rate,
        "priority_tier_high_conviction_resolved": hi_n,
        "priority_tier_high_conviction_ci_low_pct": hi_lo,
        "priority_tier_high_conviction_ci_high_pct": hi_hi,
        "priority_tier_standard_hit_rate_pct": st_rate,
        "priority_tier_standard_resolved": st_n,
        "priority_tier_standard_ci_low_pct": st_lo,
        "priority_tier_standard_ci_high_pct": st_hi,
    }


def test_live_measurement_20260909_is_inconclusive_not_critical():
    """Die realen Zahlen vom 2026-09-09 duerfen keinen critical-Alarm ausloesen."""
    verdict = classify_priority_tier_lift(
        _quality(
            high=(69.64, 112, 60.59, 77.39),
            standard=(77.78, 54, 65.06, 86.80),
            lift=-8.14,
        )
    )
    assert verdict["verdict"] == "priority_inconclusive"
    assert verdict["quality_status"] == "warning"
    assert verdict["significant"] is False
    # Der Lift selbst bleibt sichtbar — er wird nicht wegretuschiert.
    assert verdict["lift_pct"] == -8.14


def test_disjoint_and_negative_is_a_real_underperformance():
    verdict = classify_priority_tier_lift(
        _quality(
            high=(40.0, 120, 31.0, 49.0),
            standard=(80.0, 120, 72.0, 86.0),
            lift=-40.0,
        )
    )
    assert verdict["verdict"] == "priority_underperforming"
    assert verdict["quality_status"] == "critical"
    assert verdict["significant"] is True


def test_disjoint_and_positive_is_validated():
    verdict = classify_priority_tier_lift(
        _quality(
            high=(85.0, 120, 77.0, 91.0),
            standard=(50.0, 120, 41.0, 59.0),
            lift=35.0,
        )
    )
    assert verdict["verdict"] == "priority_validated"
    assert verdict["quality_status"] == "ok"
    assert verdict["significant"] is True


def test_missing_lift_is_insufficient_data():
    verdict = classify_priority_tier_lift({"priority_tier_lift_pct": None})
    assert verdict["verdict"] == "insufficient_data"
    assert verdict["significant"] is False


def test_missing_confidence_intervals_never_claim_significance():
    """Ohne CIs darf kein Alarm behauptet werden — auch bei grossem Lift nicht."""
    verdict = classify_priority_tier_lift(
        {
            "priority_tier_lift_pct": -40.0,
            "priority_tier_high_conviction_resolved": 120,
            "priority_tier_standard_resolved": 120,
        }
    )
    assert verdict["significant"] is False
    assert verdict["verdict"] == "priority_inconclusive"
    assert verdict["quality_status"] == "warning"
