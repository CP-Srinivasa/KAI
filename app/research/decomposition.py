"""Pflicht-Zerlegung für aggregierte Kennzahlen (Operator-Direktive 2026-08-08).

**Kein Aggregat ohne Zerlegung.** Eine aggregierte Zahl darf nicht berichtet,
gespeichert oder versiegelt werden, ohne offenzulegen, *was sie trägt*. Was
verdeckt wird, kann nicht geprüft werden — und was nicht geprüft werden kann,
ist keine Evidenz.

Der Anlass sind zwei reale Verdeckungen im Projekt:

* **canonical-edge:** P(µ_net>0) = 10,44 %, **ohne den besten Trade 3,50 %.**
  Ein einzelner Trade trug das Ergebnis. Dafür existiert bereits ein Gegenmittel
  (``edge_validation_gate`` ``outlier_robust`` / ``evidence_window``
  ``result_without_best_trade``) — aber nur für DIESE eine Kennzahl.
* **H2-Nachfolger, 2026-08-08:** Konkordanz 66,7 % sah solide aus. Die
  Zellauszählung ergab hit_win=8 / hit_loss=10 / miss_win=1 / miss_loss=14 —
  der Wert kam fast vollständig von der miss-Seite, während die eigentlich
  interessierende Konversion bei 44,4 % lag. Beinahe wäre diese Metrik
  versiegelt worden; dass es auffiel, war genaues Hinsehen, kein Verfahren.

Dieses Modul macht die Zerlegung generisch und maschinell erzeugbar, damit sie
nicht mehr von der Aufmerksamkeit des jeweiligen Auswerters abhängt. Rein,
ohne I/O, ohne Zustand. Der zugehörige Vertrag (jeder Evaluator MUSS einen
``decomposition``-Block liefern) wird von
``tests/unit/test_decomposition_contract.py`` erzwungen.

Gelesen wird das Ergebnis von Menschen: die ``flags`` sind ausformulierte
Warnsätze, keine Codes — ein Report, den man überfliegt, muss die Gefahr im
Klartext zeigen.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from math import fsum
from typing import Any

# Ab dieser Spannweite zwischen bester und schlechtester Gruppe gilt eine Rate
# als gruppengetrieben: das Aggregat beschreibt dann keine einheitliche
# Population mehr, sondern mittelt zwei verschiedene Regime zusammen.
GROUP_DISPARITY_PP = 0.25
# Ab diesem Anteil dominiert eine Gruppe das Aggregat schlicht durch Masse.
GROUP_DOMINANCE_SHARE = 0.60
# Entscheidungsschwelle einer ±1-Rate (P(positiv) > 0,5 = besser als Münzwurf).
DECISION_THRESHOLD = 0.5


def decompose_rate[T](
    units: Sequence[T],
    *,
    group_of: Callable[[T], str],
    is_positive: Callable[[T], bool],
) -> dict[str, Any]:
    """Zerlegt eine Erfolgsquote nach Gruppen und prüft, wer sie trägt.

    Liefert neben der Gesamtquote je Gruppe n/Treffer/Quote/Anteil sowie
    ``flags`` im Klartext. Der wichtigste Fall: entfernt man die stärkste
    Gruppe und fällt die Quote dadurch unter die Entscheidungsschwelle, dann
    trug **eine Gruppe** das Ergebnis und nicht die Population — genau der
    Konkordanz-Fall vom 2026-08-08.

    Leere Eingabe ist zulässig und liefert ``n=0`` mit leeren Gruppen (fail-
    closed: ein Aggregat ohne Einheiten behauptet nichts).
    """
    n = len(units)
    if n == 0:
        return {
            "n": 0,
            "rate": None,
            "by_group": {},
            "concentration": None,
            "leave_one_group_out_worst": None,
            "flags": [],
        }

    positives = sum(1 for u in units if is_positive(u))
    rate = positives / n

    groups: dict[str, dict[str, Any]] = {}
    for u in units:
        g = group_of(u)
        cell = groups.setdefault(g, {"n": 0, "positives": 0})
        cell["n"] += 1
        if is_positive(u):
            cell["positives"] += 1
    for cell in groups.values():
        cell["rate"] = round(cell["positives"] / cell["n"], 4)
        cell["share_of_units"] = round(cell["n"] / n, 4)

    flags: list[str] = []
    by_group = dict(sorted(groups.items()))

    top_group = max(by_group.items(), key=lambda kv: kv[1]["n"])
    concentration = {
        "top_group": top_group[0],
        "top_group_share": top_group[1]["share_of_units"],
    }
    if top_group[1]["share_of_units"] >= GROUP_DOMINANCE_SHARE:
        flags.append(
            f"Gruppe {top_group[0]!r} stellt "
            f"{top_group[1]['share_of_units']:.0%} aller Einheiten — das Aggregat "
            f"beschreibt im Wesentlichen diese Gruppe."
        )

    # Welche Gruppe STÜTZT das Ergebnis am stärksten? Nicht die größte nach
    # Masse — die, deren Entfernung die Quote am tiefsten drückt. Genau hier
    # lag ein Denkfehler im ersten Entwurf: bei der 2026-08-08-Konkordanz ist
    # die massenstärkste Gruppe (hit, n=18) die SCHWACHE; getragen wurde das
    # Aggregat von miss (14/15). Wer nach Masse entfernt, verfehlt den Fall.
    worst: dict[str, Any] | None = None
    if len(by_group) > 1:
        candidates = []
        for g in by_group:
            rest = [u for u in units if group_of(u) != g]
            if not rest:
                continue
            rest_rate = sum(1 for u in rest if is_positive(u)) / len(rest)
            candidates.append((rest_rate, g, len(rest)))
        if candidates:
            rest_rate, g, rest_n = min(candidates)
            worst = {"group": g, "n": rest_n, "rate": round(rest_rate, 4)}
            if (rate > DECISION_THRESHOLD) != (rest_rate > DECISION_THRESHOLD):
                flags.append(
                    f"WARNUNG: ohne Gruppe {g!r} faellt die Quote von {rate:.1%} "
                    f"auf {rest_rate:.1%} und wechselt die Seite der "
                    f"Entscheidungsschwelle ({DECISION_THRESHOLD:.0%}) — das "
                    f"Ergebnis wird von dieser Gruppe getragen, nicht von der "
                    f"Population."
                )

    if len(by_group) > 1:
        rates = [c["rate"] for c in by_group.values()]
        spread = max(rates) - min(rates)
        if spread >= GROUP_DISPARITY_PP:
            best_rate_group = max(by_group.items(), key=lambda kv: kv[1]["rate"])
            worst_rate_group = min(by_group.items(), key=lambda kv: kv[1]["rate"])
            flags.append(
                f"Gruppen laufen auseinander: {best_rate_group[0]!r} "
                f"{best_rate_group[1]['rate']:.1%} vs {worst_rate_group[0]!r} "
                f"{worst_rate_group[1]['rate']:.1%} ({spread:.0%} Spannweite) — "
                f"die Gesamtquote mittelt verschiedene Regime zusammen."
            )

    return {
        "n": n,
        "rate": round(rate, 4),
        "by_group": by_group,
        "concentration": concentration,
        "leave_one_group_out_worst": worst,
        "flags": flags,
    }


def decompose_mean(
    values: Sequence[float],
    *,
    labels: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Zerlegt einen Mittelwert und prüft, ob eine Einheit ihn trägt.

    Verallgemeinert die bestehende ``result_without_best_trade``-Logik aus
    ``evidence_window``: fällt das Vorzeichen weg, sobald der größte Beitrag
    entfernt wird, ist der Mittelwert ein Artefakt eines Ausreißers.
    ``labels`` benennt optional die Einheiten, damit der Bericht sagen kann,
    *welche* das Ergebnis trägt.
    """
    n = len(values)
    if n == 0:
        return {"n": 0, "mean": None, "without_top": None, "top_contributor": None, "flags": []}

    mean = fsum(values) / n
    top_idx = max(range(n), key=lambda i: values[i])
    top_label = labels[top_idx] if labels is not None and top_idx < len(labels) else None

    flags: list[str] = []
    without_top: dict[str, Any] | None = None
    if n >= 2:
        rest = [v for i, v in enumerate(values) if i != top_idx]
        rest_mean = fsum(rest) / len(rest)
        without_top = {"n": len(rest), "mean": round(rest_mean, 6)}
        if (mean > 0) != (rest_mean > 0):
            named = f" ({top_label})" if top_label else ""
            flags.append(
                f"WARNUNG: ohne den groessten Einzelbeitrag{named} dreht der "
                f"Mittelwert von {mean:+.4f} auf {rest_mean:+.4f} — das Ergebnis "
                f"wird von einer Einheit getragen, nicht von einem Prozess."
            )

    return {
        "n": n,
        "mean": round(mean, 6),
        "without_top": without_top,
        "top_contributor": {
            "index": top_idx,
            "label": top_label,
            "value": round(values[top_idx], 6),
        },
        "flags": flags,
    }


__all__ = [
    "DECISION_THRESHOLD",
    "GROUP_DISPARITY_PP",
    "GROUP_DOMINANCE_SHARE",
    "decompose_mean",
    "decompose_rate",
]
