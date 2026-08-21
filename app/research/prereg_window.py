"""Die Ablaufregel T0 -> T1 -> T2, mechanisch statt nach Ermessen.

Optional Stopping ist der Fehler, gegen den diese Datei gebaut ist. Er sieht
harmlos aus::

    Tag 61:  n_valid = 104,  G = 53   -> "reif! jetzt auswerten"

Das waere kein frueher Erfolg, sondern ein anderer Test: wer zusieht und
auswertet, sobald es reif *und* huebsch ist, hat die Irrtumswahrscheinlichkeit
still erhoeht. Deshalb ist **T1 der erste Entscheidungszeitpunkt** — auch wenn
die Reife schon an Tag 40 erreicht war.

Bis T1 duerfen ausschliesslich **blinde** Groessen beobachtet werden::

    erlaubt:   n_valid · n_clusters · data coverage · provider health ·
               universe integrity
    verboten:  mean_bps · p_value · hit rate · "sieht gut aus"

Das ist hier nicht nur Vorsatz, sondern Bauweise: ``WindowDecision`` traegt
ausschliesslich Reifezahlen, und ``assert_evaluable`` wirft, wenn jemand
ausserhalb eines Entscheidungszeitpunkts eine Performance-Zahl anfordert. Man
kann den p-Wert nicht aus Versehen bekommen.

Die Kette:

======================  ============================================
Zeitpunkt               Aktion
======================  ============================================
now < T1                WAIT
now >= T1, T1 offen     reif -> EVALUATE  ·  sonst -> EXTEND_TO_T2
T1 verlaengert, < T2    WAIT
now >= T2               reif -> EVALUATE  ·  sonst -> INCONCLUSIVE
Verdikt schon gefaellt  CLOSED
======================  ============================================

Der entscheidende Punkt ist die dritte Zeile: nach einer Verlaengerung wird
**nicht** wieder ausgewertet, sobald die Reife zufaellig erreicht ist. Sonst
waere die Verlaengerung nur eine zweite Gelegenheit zum Hinsehen — optional
stopping durch die Hintertuer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

ACTION_WAIT = "WAIT"
ACTION_EVALUATE = "EVALUATE"
ACTION_EXTEND_TO_T2 = "EXTEND_TO_T2"
ACTION_INCONCLUSIVE = "INCONCLUSIVE_NOT_MATURE"
ACTION_CLOSED = "CLOSED_VERDICT_ALREADY_ISSUED"
# Der Entschluss zu werten steht, das Verdikt fehlt. NICHT geschlossen und
# ausdruecklich KEINE neue Datenaufnahme: es wird exakt der eingefrorene
# Datenschnitt erneut ausgewertet.
ACTION_RESUME_EVALUATION = "RESUME_EVALUATION"

CHECKPOINT_PRE_T1 = "PRE_T1"
CHECKPOINT_T1 = "T1"
CHECKPOINT_BETWEEN = "BETWEEN_T1_AND_T2"
CHECKPOINT_T2 = "T2"


class PrematureEvaluationError(RuntimeError):
    """Es wurde eine Performance-Zahl ausserhalb eines Entscheidungszeitpunkts verlangt."""


@dataclass(frozen=True)
class MaturityCounts:
    """Ausschliesslich blinde Groessen. Hier steht bewusst kein Mittelwert.

    Wer diese Klasse um ``mean_bps`` erweitert, hebt die Trennung auf, die das
    ganze Modul traegt.
    """

    n_valid: int
    n_clusters: int
    raw_fires: int = 0
    label_capable_fires: int = 0
    data_unavailable_count: int = 0
    symbols_with_valid_signals: int = 0


@dataclass(frozen=True)
class WindowDecision:
    """Was jetzt zu tun ist — und warum."""

    action: str
    checkpoint: str
    mature: bool
    counts: MaturityCounts
    reasons: tuple[str, ...] = ()

    @property
    def may_evaluate(self) -> bool:
        return self.action in (ACTION_EVALUATE, ACTION_RESUME_EVALUATION)

    @property
    def must_use_frozen_input(self) -> bool:
        """Bei einer Wiederaufnahme sind aktuelle Daten verboten.

        Zwischen dem Entschluss und dem Neustart kann der Provider eine
        nachgelieferte Kerze oder ein korrigiertes Volumen liefern. Wer dann neu
        laedt, wertet nicht dieselbe Auswertung erneut aus, sondern eine zweite.
        """
        return self.action == ACTION_RESUME_EVALUATION


def _parse(timestamp_utc: str) -> datetime:
    """Zeitzonenbehaftet, sonst Abbruch.

    ``activate()`` wurde bereits so gehaertet; dasselbe Prinzip muss durchgaengig
    gelten. Ein still als UTC gelesener Zeitstempel kann ein Fenster um Stunden
    verschieben — und damit die Menge der Signale, die ueberhaupt hineinfallen.
    """
    parsed = datetime.fromisoformat(timestamp_utc)
    if parsed.tzinfo is None:
        raise ValueError(
            f"{timestamp_utc!r} ist zeitzonenlos — UTC wird nicht geraten. "
            "Zeitzone ausdruecklich angeben."
        )
    return parsed.astimezone(UTC)


def decide_window_action(
    *,
    now_utc: str,
    t1_utc: str,
    t2_utc: str,
    counts: MaturityCounts,
    n_valid_min: int,
    cluster_min: int,
    t1_outcome: str | None = None,
    verdict_recorded: bool = False,
) -> WindowDecision:
    """Rein und zustandslos. Der T1-Ausgang wird vom Aufrufer mitgegeben.

    Args:
        now_utc: Jetzt (ISO-8601).
        t1_utc / t2_utc: die versiegelten Entscheidungszeitpunkte.
        counts: die blinden Reifezahlen.
        n_valid_min / cluster_min: die beiden versiegelten Reifeschranken.
        t1_outcome: was an T1 entschieden wurde — ``None`` (T1 noch nicht
            erreicht bzw. noch nicht festgehalten), ``EVALUATE`` oder
            ``EXTEND_TO_T2``. Dass der Aufrufer das persistieren muss, ist
            Absicht: der T1-Ausgang ist ein Ereignis, keine Ableitung.

    Returns:
        WindowDecision mit genau einer Aktion.
    """
    now = _parse(now_utc)
    t1 = _parse(t1_utc)
    t2 = _parse(t2_utc)

    reasons: list[str] = []
    if counts.n_valid < n_valid_min:
        reasons.append(f"n_valid={counts.n_valid} < n_valid_min={n_valid_min}")
    if counts.n_clusters < cluster_min:
        reasons.append(f"clusters={counts.n_clusters} < cluster_min={cluster_min}")
    mature = not reasons

    if t1_outcome == ACTION_EVALUATE:
        if verdict_recorded:
            return WindowDecision(
                action=ACTION_CLOSED,
                checkpoint=CHECKPOINT_T1,
                mature=mature,
                counts=counts,
                reasons=("Verdikt wurde an T1 bereits gefaellt — genau einmal.",),
            )
        # Entschluss steht, Verdikt fehlt: ein Absturz zwischen beidem darf das
        # Ergebnis nicht verschlucken. Wiederaufnehmen — aber ausschliesslich auf
        # dem eingefrorenen Datenschnitt, nie auf aktuellen Daten.
        return WindowDecision(
            action=ACTION_RESUME_EVALUATION,
            checkpoint=CHECKPOINT_T1,
            mature=mature,
            counts=counts,
            reasons=(
                "EVALUATE steht im Journal, ein Verdikt nicht — "
                "exakt den eingefrorenen Datenschnitt erneut auswerten.",
            ),
        )

    if now < t1:
        # Auch bei laengst erreichter Reife. Genau das ist der Schutz.
        note = (
            "Reife bereits erreicht, aber T1 ist der erste Entscheidungszeitpunkt."
            if mature
            else "vor T1"
        )
        return WindowDecision(
            action=ACTION_WAIT,
            checkpoint=CHECKPOINT_PRE_T1,
            mature=mature,
            counts=counts,
            reasons=(note,),
        )

    if now < t2:
        if t1_outcome == ACTION_EXTEND_TO_T2:
            return WindowDecision(
                action=ACTION_WAIT,
                checkpoint=CHECKPOINT_BETWEEN,
                mature=mature,
                counts=counts,
                reasons=("an T1 verlaengert — naechster Entscheidungszeitpunkt ist T2",),
            )
        if mature:
            return WindowDecision(
                action=ACTION_EVALUATE, checkpoint=CHECKPOINT_T1, mature=True, counts=counts
            )
        return WindowDecision(
            action=ACTION_EXTEND_TO_T2,
            checkpoint=CHECKPOINT_T1,
            mature=False,
            counts=counts,
            reasons=(*reasons, "KEINE Performance ansehen — mechanisch verlaengern"),
        )

    if mature:
        return WindowDecision(
            action=ACTION_EVALUATE, checkpoint=CHECKPOINT_T2, mature=True, counts=counts
        )
    return WindowDecision(
        action=ACTION_INCONCLUSIVE,
        checkpoint=CHECKPOINT_T2,
        mature=False,
        counts=counts,
        reasons=(*reasons, "Fristende ohne Reife ist INCONCLUSIVE, ausdruecklich nicht NOT_MET"),
    )


def assert_evaluable(decision: WindowDecision) -> None:
    """Torwaechter vor jeder Performance-Rechnung.

    Fail-closed: lieber ein Abbruch als ein p-Wert, den niemand haette sehen
    duerfen. Einmal gesehen, laesst er sich nicht zurueckziehen.
    """
    if not decision.may_evaluate:
        raise PrematureEvaluationError(
            f"action={decision.action} at checkpoint={decision.checkpoint} — "
            "Performance darf hier nicht berechnet werden. "
            + (" ".join(decision.reasons) if decision.reasons else "")
        )
