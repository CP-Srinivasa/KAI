"""Die eine praeregistrierte Regel — allein in ihrer eigenen Datei.

Sie stand vorher in ``runner.py``, und das erzwang eine schlechte Wahl beim
Evaluator-Bundle: die ganze Datei hashen (dann bricht jede unbeteiligte
Runner-Aenderung den Seal) oder nur den Funktionstext (dann faellt eine
geaenderte Konstante NICHT auf).

Gemessen am 2026-08-25, bevor diese Datei entstand::

    RSI_REENTRY_LOW = 30.0 -> 15.0    Bundle-Hash unveraendert  !!
    VOLUME_SPIKE_Z  = 2.0  -> 1.0     Bundle-Hash unveraendert  !!

Beides aendert die Regel fundamental. ``inspect.getsource`` einer Funktion
enthaelt die Konstanten nicht, die sie liest, und ``git rev-parse HEAD`` sieht
uncommittete Aenderungen im Arbeitsbaum ueberhaupt nicht — der Bundle-Hash ist
die einzige Verteidigung dagegen.

Eine eigene Datei loest den Zielkonflikt: hier steht die Regel und sonst nichts,
also darf sie vollstaendig gehasht werden. Eine Aenderung an der Regel aendert
den Seal immer, eine Aenderung am Runner nie.

``VOLUME_SPIKE_Z`` liegt weiterhin bei den Indikatoren — dort gehoert es hin,
denn es definiert das Feature, nicht die Regel. Stattdessen wandert
``app/analysis/indicators/volume_z.py`` mit ins Bundle.
"""

from __future__ import annotations

from app.analysis.features.feature_matrix import FeatureRow
from app.analysis.indicators.volume_z import VOLUME_SPIKE_Z
from app.research.samples import Decider

# RSI-Grenzen des Re-Entry. Versiegelt mit der Spec 2026-08-19; keine
# Stellschrauben. Sie stehen hier und nicht im Runner, damit ihre Aenderung den
# Evaluator-Bundle-Hash zwingend bricht.
RSI_REENTRY_LOW = 30.0
RSI_REENTRY_HIGH = 70.0

PRIMARY_CONFIRMATORY_NAME = "rsi_reentry_volume_confirmed"


def rsi_reentry_volume_confirmed(r: FeatureRow) -> int:
    """RSI verlaesst die Extremzone auf einem Volumen-Spike — einmal je Uebergang.

    ::

        LONG   <=>  rsi_14_prev < 30  AND  rsi_14 >= 30  AND  volume_z_20 >= 2.0
        SHORT  <=>  rsi_14_prev > 70  AND  rsi_14 <= 70  AND  volume_z_20 >= 2.0

    Das ist ein RE-ENTRY aus dem Extrem, nicht der Eintritt hinein — weshalb die
    gemessenen -23,27 bps von ``rsi_oversold_long`` sie nicht widerlegen: jene
    Regel misst das LEVEL ``rsi_14 < 30`` und feuert damit in jeder Kerze des
    Zustands. Einmal je Uebergang zu feuern ist der Unterschied, der bei einem
    Buch zaehlt, dessen Verlust fast vollstaendig Gebuehr ist.

    Wirklich neu ist allein die KONJUNKTION mit dem Volumen-Spike.
    """
    if r.rsi_14 is None or r.rsi_14_prev is None or r.volume_z_20 is None:
        return 0
    if r.volume_z_20 < VOLUME_SPIKE_Z:
        return 0
    if r.rsi_14_prev < RSI_REENTRY_LOW <= r.rsi_14:
        return 1
    if r.rsi_14_prev > RSI_REENTRY_HIGH >= r.rsi_14:
        return -1
    return 0


def primary_confirmatory_hypothesis() -> list[tuple[str, Decider]]:
    """Die EINZIGE Hypothese, die aus diesem Fenster ein Verdikt erhalten kann.

    Ein Mitglied, also reduziert sich BH-FDR auf ``p <= alpha``. Das ist kein
    schwacher Schutz: steht vor T0 genau eine Hypothese fest und wird sie
    ausschliesslich auf Daten nach T0 beurteilt, existiert innerhalb des
    Experiments kein Multiple-Testing-Problem. Die Selektionsverzerrung aus der
    Entstehungsgeschichte wird dadurch entschaerft, dass die Regel vorher
    eingefroren und danach auf neuen Daten geprueft wird — nicht durch eine
    kuenstliche Strafe.
    """
    return [(PRIMARY_CONFIRMATORY_NAME, rsi_reentry_volume_confirmed)]
