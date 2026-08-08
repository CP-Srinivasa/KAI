"""Zerlegung aggregierter Kennzahlen — inkl. der beiden realen Verdeckungsfälle.

Die zwei wichtigsten Tests hier sind Regressionen auf echte Vorfälle:

* ``test_catches_the_2026_08_08_concordance_asymmetry`` — die Konkordanz, die
  mit 66,7 % solide aussah, aber fast vollständig von der miss-Seite getragen
  war. Sie wurde damals nur durch genaues Hinsehen entdeckt.
* ``test_catches_the_canonical_edge_best_trade_case`` — der Edge, dessen
  Mittelwert an einem einzigen Trade hing.

Fängt die Zerlegung diese beiden nicht, ist sie wertlos.
"""

from __future__ import annotations

from app.research.decomposition import (
    DECISION_THRESHOLD,
    decompose_mean,
    decompose_rate,
)


def _units(spec: dict[str, tuple[int, int]]) -> list[tuple[str, bool]]:
    """``{gruppe: (positiv, negativ)}`` → Einheitenliste."""
    out: list[tuple[str, bool]] = []
    for group, (pos, neg) in spec.items():
        out += [(group, True)] * pos
        out += [(group, False)] * neg
    return out


def _rate(units: list[tuple[str, bool]]) -> dict:
    return decompose_rate(units, group_of=lambda u: u[0], is_positive=lambda u: u[1])


# ── Die realen Verdeckungsfälle ──────────────────────────────────────────────


def test_catches_the_2026_08_08_concordance_asymmetry() -> None:
    """Konkordanz 22/33 = 66,7 %, getragen von der miss-Seite.

    Zellen: hit_win=8 hit_loss=10 (konkordant 8/18 = 44,4 %),
    miss_win=1 miss_loss=14 (konkordant 14/15 = 93,3 %).
    Das Aggregat sieht solide aus; die hit-Seite liegt unter dem Münzwurf.
    """
    units = _units({"hit": (8, 10), "miss": (14, 1)})
    dec = _rate(units)

    assert dec["n"] == 33
    assert dec["rate"] == round(22 / 33, 4)  # 66,7 % — sieht gut aus
    assert dec["by_group"]["hit"]["rate"] == round(8 / 18, 4)  # 44,4 %
    assert dec["by_group"]["miss"]["rate"] == round(14 / 15, 4)  # 93,3 %

    # Der Punkt: die Spannweite MUSS als Flag erscheinen.
    assert dec["flags"], "Die Asymmetrie muss im Klartext gemeldet werden"
    assert any("auseinander" in f for f in dec["flags"])

    # Und ohne die stärkste Gruppe kippt das Urteil unter die Schwelle.
    assert dec["leave_one_group_out_worst"]["group"] == "miss"
    assert dec["leave_one_group_out_worst"]["rate"] < DECISION_THRESHOLD
    assert any("getragen" in f for f in dec["flags"])


def test_catches_the_canonical_edge_best_trade_case() -> None:
    """Mittelwert positiv, ohne den besten Trade negativ (canonical-edge-Muster)."""
    values = [-3.0, -2.0, -1.5, -1.0, 40.0]
    dec = decompose_mean(values, labels=["a", "b", "c", "d", "monster-trade"])

    assert dec["mean"] > 0
    assert dec["without_top"]["mean"] < 0
    assert dec["flags"], "Ein Vorzeichenwechsel ohne Top-Beitrag ist berichtspflichtig"
    assert any("getragen" in f for f in dec["flags"])
    assert dec["top_contributor"]["label"] == "monster-trade"


# ── Normalfälle: keine Fehlalarme ────────────────────────────────────────────


def test_homogeneous_groups_produce_no_flags() -> None:
    """Gleichmäßig verteilte, ähnliche Gruppen dürfen NICHT warnen.

    Eine Wache, die immer schreit, wird ignoriert (die TL-008-Lehre).
    """
    units = _units({"a": (12, 8), "b": (11, 9), "c": (12, 8)})
    dec = _rate(units)

    assert dec["n"] == 60
    assert dec["flags"] == []


def test_stable_mean_produces_no_flag() -> None:
    dec = decompose_mean([2.0, 2.5, 1.8, 2.2, 3.0])
    assert dec["mean"] > 0
    assert dec["without_top"]["mean"] > 0
    assert dec["flags"] == []


# ── Struktur und Randfälle ───────────────────────────────────────────────────


def test_groups_partition_the_population_exactly() -> None:
    units = _units({"x": (5, 3), "y": (2, 6)})
    dec = _rate(units)

    assert sum(c["n"] for c in dec["by_group"].values()) == dec["n"]
    assert abs(sum(c["share_of_units"] for c in dec["by_group"].values()) - 1.0) < 1e-9


def test_dominant_group_is_flagged_even_without_sign_flip() -> None:
    """Masse-Dominanz ist auch dann berichtspflichtig, wenn das Urteil hält."""
    units = _units({"big": (90, 10), "small": (4, 1)})
    dec = _rate(units)

    assert dec["concentration"]["top_group"] == "big"
    assert dec["concentration"]["top_group_share"] >= 0.9
    assert any("stellt" in f for f in dec["flags"])


def test_empty_population_is_fail_closed() -> None:
    dec = _rate([])
    assert dec["n"] == 0
    assert dec["rate"] is None
    assert dec["by_group"] == {}
    assert dec["flags"] == []


def test_single_group_has_no_without_top_and_no_disparity() -> None:
    """Eine einzige Gruppe kann weder auseinanderlaufen noch übrig bleiben."""
    dec = _rate(_units({"only": (6, 4)}))

    assert dec["leave_one_group_out_worst"] is None
    assert not any("auseinander" in f for f in dec["flags"])


def test_single_value_mean_has_no_leave_one_out() -> None:
    dec = decompose_mean([5.0])
    assert dec["n"] == 1
    assert dec["without_top"] is None
    assert dec["flags"] == []


def test_empty_mean_is_fail_closed() -> None:
    dec = decompose_mean([])
    assert dec["n"] == 0
    assert dec["mean"] is None
    assert dec["flags"] == []
