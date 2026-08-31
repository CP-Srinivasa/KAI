"""Welche terminale Klasse ein Claim tragen DARF — mit Grund für jede Ablehnung.

Entstanden am 2026-08-31 an K1 (``00c75a76a2b0e78b``). Die Herleitung — vier
Klassen ausgeschlossen, eine zugelassen — musste damals von Hand aus zwei
Dokumenten gelesen werden und war damit wiederholbar **mit anderem Ergebnis**.

Der teure Fehler droht in eine bestimmte Richtung: soll ein Claim
administrativ geschlossen werden, ist die Versuchung gross, eine vorhandene
Klasse passend zu machen. ``SUPERSEDED`` sieht harmlos aus, behauptet aber
einen Nachfolger; ``CLOSED_UNMEASURABLE`` sieht bescheiden aus, behauptet aber
Unmöglichkeit. Beides sind Tatsachenbehauptungen, die belegt sein müssen.

Dieses Modul erfindet **keine** Klasse. Alle hier geführten stehen bereits im
Vertrag (``config/prereg_supervision.json`` und
``app/research/prereg_maturity.py``); es entscheidet nur, welche davon ein
gegebener Sachverhalt tragen kann — und liefert zu jedem Nein den Grund mit.

Nicht enthalten sind ``RETIRE`` und ``NO_WATCH_REQUIRED``: beide tragen im
Register wörtlich die Definition „Nicht vergeben." Ein Name ohne Definition
darf nicht auswählbar sein, sonst wird er irgendwann benutzt und dabei erfunden.

Rein, ohne I/O, ohne Uhr — die Fakten kommen von aussen herein.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Die auswählbaren terminalen Klassen. Bewusst geschlossen.
TERMINAL_CLASSES: tuple[str, ...] = (
    "MET",
    "NOT_MET",
    "INCONCLUSIVE_BY_TIMEOUT",
    "CLOSED_UNMEASURABLE",
    "SUPERSEDED",
    "SCHEDULED_REVIEW_COMPLETED",
)


@dataclass(frozen=True)
class ClaimFacts:
    """Die Tatsachen, die über die Zulässigkeit entscheiden — alle belegbar.

    ``population_provably_unevaluable`` ist bewusst so benannt: nicht „wurde
    nicht gemessen", sondern **nachgewiesen nicht auswertbar**. Der Unterschied
    ist der zwischen ``6751bc33`` (0 von 19 Dokumenten verwertbar, egal wie
    lange man wartet) und K1 (nicht erhoben, weil nicht weiterverfolgt).
    """

    window_closed: bool
    substantive_evaluation_performed: bool
    population_provably_unevaluable: bool
    successor_prereg_id: str | None
    successor_terminal_verdict: str | None
    previous_decision_state: str | None


def admissible_terminal_classes(facts: ClaimFacts) -> dict[str, tuple[bool, str]]:
    """Je Klasse ``(zulässig, Begründung)`` — die Begründung gilt in beide Richtungen."""
    if not facts.window_closed:
        grund = (
            "Fenster ist noch offen — vor Fensterschluss ist KEIN terminaler "
            "Abschluss zulässig, auch kein Sachverdikt."
        )
        return dict.fromkeys(TERMINAL_CLASSES, (False, grund))

    out: dict[str, tuple[bool, str]] = {}

    # --- Sachverdikte: nur mit tatsächlicher Auswertung ----------------------
    if facts.substantive_evaluation_performed:
        sach = (True, "Der Claim wurde gegen die versiegelte Regel ausgewertet.")
    else:
        sach = (
            False,
            "Der Claim wurde nicht ausgewertet — ein Sachverdikt waere unbelegt. "
            "'Nicht gemessen' ist weder MET noch NOT_MET.",
        )
    out["MET"] = sach
    out["NOT_MET"] = sach

    # --- Fristablauf ohne Sachverdikt ---------------------------------------
    if facts.substantive_evaluation_performed:
        out["INCONCLUSIVE_BY_TIMEOUT"] = (
            False,
            "Es liegt eine Auswertung vor — dann gilt ihr Ergebnis, nicht der Fristablauf.",
        )
    else:
        out["INCONCLUSIVE_BY_TIMEOUT"] = (
            True,
            "Fenster geschlossen und kein Sachverdikt gebildet. Die Klasse "
            "behauptet weder einen Nachfolger noch Unmöglichkeit noch ein "
            "Ergebnis — nur ein Ende.",
        )

    # --- Unmessbarkeit: verlangt einen BELEG, keine Entscheidung -------------
    if facts.population_provably_unevaluable:
        out["CLOSED_UNMEASURABLE"] = (
            True,
            "Die Population ist nachweislich nicht auswertbar — die Unmöglichkeit "
            "ist belegt, nicht angenommen.",
        )
    else:
        out["CLOSED_UNMEASURABLE"] = (
            False,
            "Unmessbarkeit ist nicht erwiesen. Dass eine Messung unterbleibt, ist "
            "eine Entscheidung und kein Beweis der Unmöglichkeit.",
        )

    # --- Ersetzung: verlangt BEIDE Pflichtfelder ----------------------------
    if facts.successor_prereg_id and facts.successor_terminal_verdict:
        out["SUPERSEDED"] = (
            True,
            "Nachfolge-Registrierung und deren terminales Verdikt liegen vor.",
        )
    else:
        fehlt = []
        if not facts.successor_prereg_id:
            fehlt.append("superseded_by")
        if not facts.successor_terminal_verdict:
            fehlt.append("successor_terminal_verdict")
        out["SUPERSEDED"] = (
            False,
            "Es existiert keine vollstaendige Nachfolge-Registrierung; es fehlt: "
            + ", ".join(fehlt)
            + ". Ein thematisch verwandter Claim ist kein Nachfolger.",
        )

    # --- Vollzogene terminierte Wiedervorlage --------------------------------
    if (
        facts.previous_decision_state == "MANUAL_SCHEDULED_REVIEW"
        and facts.substantive_evaluation_performed
    ):
        out["SCHEDULED_REVIEW_COMPLETED"] = (
            True,
            "Der Claim stand auf MANUAL_SCHEDULED_REVIEW und der Review wurde durchgefuehrt.",
        )
    else:
        out["SCHEDULED_REVIEW_COMPLETED"] = (
            False,
            "Verlangt die Kette MANUAL_SCHEDULED_REVIEW -> Review durchgefuehrt "
            "-> terminaler Abschluss; diese Kette liegt nicht vor.",
        )

    return out


def recommended_terminal_class(facts: ClaimFacts) -> str | None:
    """Die Klasse, wenn sie EINDEUTIG ist — sonst ``None``.

    Bewusst keine Rangfolge: bleiben mehrere zulaessig, ist das eine
    Entscheidung des Operators und keine, die eine Prioritaetsliste treffen
    darf. Eine automatisch gewaehlte Klasse waere genau die stille Festlegung,
    die dieses Modul verhindern soll.
    """
    zulaessig = [name for name, (ok, _) in admissible_terminal_classes(facts).items() if ok]
    return zulaessig[0] if len(zulaessig) == 1 else None


__all__ = [
    "TERMINAL_CLASSES",
    "ClaimFacts",
    "admissible_terminal_classes",
    "recommended_terminal_class",
]
