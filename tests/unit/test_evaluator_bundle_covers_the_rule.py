r"""Was die Regel definiert, MUSS den Seal brechen — und sonst nichts.

Befund am 2026-08-25, an der eigenen Implementierung gemessen. Eine fruehere
Fassung hashte aus ``runner.py`` nur den Quelltext des Deciders
(``inspect.getsource``)::

    RSI_REENTRY_LOW = 30.0 -> 15.0    Bundle-Hash unveraendert  !!
    VOLUME_SPIKE_Z  = 2.0  -> 1.0     Bundle-Hash unveraendert  !!

Beide Konstanten definieren die Regel und werden zur Auswertungszeit gelesen —
aber ``getsource`` einer Funktion enthaelt die Konstanten nicht, die sie liest,
und ``VOLUME_SPIKE_Z`` lag ausserdem in einem Modul ausserhalb des Bundles.

Verschaerfend: ``git rev-parse HEAD`` sieht uncommittete Aenderungen im
Arbeitsbaum ueberhaupt nicht. Wer eine Datei editiert und nicht committet,
passiert die HEAD-Pruefung ungehindert — der Bundle-Hash ist die einzige
Verteidigung, die Bytes liest.

Der Zielkonflikt ("ganze Datei hashen" bricht bei jeder Runner-Aenderung, "nur
die Funktion" faengt Konstanten nicht) war ein Symptom der Lage: die Regel stand
in einer grossen, fremden Datei. Sie liegt jetzt allein in
``app/research/sealed_hypothesis.py``, und beide Ziele gelten gleichzeitig.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from app.research.evaluator_identity import (
    EVALUATOR_BUNDLE_MODULES,
    evaluator_bundle_sha256,
)

REPO = Path(__file__).resolve().parents[2]
_DECIDER = "rsi_reentry_volume_confirmed"


def _bundle(decider: str = _DECIDER) -> str:
    return evaluator_bundle_sha256(REPO, decider_name=decider)


@contextmanager
def _temporarily_replaced(relative: str, needle: str, replacement: str) -> Iterator[None]:
    """Eine Zeile aendern, den Hash messen, den Originalzustand zurueckgeben.

    Bewusst am ECHTEN Repo statt an einer Kopie: der Hash soll ueber genau die
    Dateien laufen, die spaeter auch die Auswertung fahren.
    """
    path = REPO / relative
    original = path.read_text(encoding="utf-8")
    assert needle in original, f"{relative}: {needle!r} nicht gefunden — Test veraltet?"
    path.write_text(original.replace(needle, replacement, 1), encoding="utf-8")
    try:
        yield
    finally:
        path.write_text(original, encoding="utf-8")


@pytest.mark.parametrize(
    ("relative", "needle", "replacement", "why"),
    [
        (
            "app/research/sealed_hypothesis.py",
            "RSI_REENTRY_LOW = 30.0",
            "RSI_REENTRY_LOW = 15.0",
            "die untere RSI-Grenze IST die Regel",
        ),
        (
            "app/research/sealed_hypothesis.py",
            "RSI_REENTRY_HIGH = 70.0",
            "RSI_REENTRY_HIGH = 85.0",
            "die obere RSI-Grenze ebenso",
        ),
        (
            "app/analysis/indicators/volume_z.py",
            "VOLUME_SPIKE_Z = 2.0",
            "VOLUME_SPIKE_Z = 1.0",
            "die Spike-Schwelle wird zur Auswertungszeit gelesen",
        ),
        (
            "app/analysis/indicators/volume_z.py",
            "VOLUME_Z_WINDOW = 20",
            "VOLUME_Z_WINDOW = 10",
            "die Baseline-Laenge definiert das Feature",
        ),
        (
            "app/research/pooled_inference.py",
            "correction = n_clusters / (n_clusters - 1)",
            "correction = 1.0",
            "die CR1-Korrektur veraendert den p-Wert",
        ),
        (
            "app/research/samples.py",
            "gross - round_trip_cost_bps",
            "gross - 0.0",
            "die Kostenarithmetik veraendert den Mittelwert",
        ),
        (
            "app/research/primary_confirmatory.py",
            "elif summary.p_value <= alpha",
            "elif summary.p_value <= alpha * 2",
            "die Verdikt-Schwelle selbst",
        ),
    ],
)
def test_changing_what_defines_the_rule_breaks_the_seal(
    relative: str, needle: str, replacement: str, why: str
) -> None:
    base = _bundle()

    with _temporarily_replaced(relative, needle, replacement):
        changed = _bundle()

    assert changed != base, f"{relative}: {why} — die Aenderung MUSS den Seal brechen"


def test_an_unrelated_runner_change_does_not_break_the_seal() -> None:
    """Die Gegenprobe. Ohne sie waere der Seal nur laestig.

    Genau dieser Punkt war der Grund, die Regel aus ``runner.py`` herauszuloesen:
    dort haette jede Kadenz- oder Universums-Aenderung den Seal gebrochen und
    damit den Anreiz erzeugt, ihn zu umgehen.
    """
    base = _bundle()

    with _temporarily_replaced(
        "app/research/runner.py", "DEFAULT_LOOKBACK_DAYS = 180", "DEFAULT_LOOKBACK_DAYS = 365"
    ):
        unrelated = _bundle()

    assert unrelated == base


def test_a_different_decider_name_breaks_the_seal() -> None:
    """Sonst waere ein Wechsel der gewerteten Regel unsichtbar."""
    assert _bundle("etwas_anderes") != _bundle()


def test_the_bundle_contains_the_rule_and_its_constants() -> None:
    """Die Mitgliedschaft ausdruecklich gepinnt, nicht nur ihre Wirkung."""
    for required in (
        "app/research/sealed_hypothesis.py",
        "app/analysis/indicators/volume_z.py",
        "app/analysis/features/feature_matrix.py",
        "app/research/samples.py",
        "app/research/pooled_inference.py",
        "app/research/primary_confirmatory.py",
    ):
        assert required in EVALUATOR_BUNDLE_MODULES, required


def test_dataset_producing_modules_are_deliberately_absent() -> None:
    """``forward_returns`` und ``rsi`` laufen bei der Auswertung nicht mehr.

    Sie erzeugen den Datenschnitt; ihr Ergebnis ist ueber ``dataset_sha256``
    gebunden. Sie ins Bundle zu nehmen wuerde den Seal brechen, ohne dass sich
    das Verdikt aus demselben Artefakt aendern koennte — die Auswahlfrage lautet
    nicht "beteiligt?", sondern "koennte es aus DEMSELBEN Artefakt ein anderes
    Verdikt erzeugen?".
    """
    for absent in (
        "app/analysis/features/forward_returns.py",
        "app/analysis/indicators/rsi.py",
    ):
        assert absent not in EVALUATOR_BUNDLE_MODULES, absent


def test_the_sealed_rule_lives_alone() -> None:
    """Nur die Regel, ihre Grenzen und ihre Familie — sonst nichts.

    Waechst diese Datei um fremde Dinge, kehrt der Zielkonflikt zurueck.
    """
    import ast

    source = (REPO / "app" / "research" / "sealed_hypothesis.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    functions = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    constants = {
        target.id
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }

    assert functions == {"rsi_reentry_volume_confirmed", "primary_confirmatory_hypothesis"}
    assert constants == {"RSI_REENTRY_LOW", "RSI_REENTRY_HIGH", "PRIMARY_CONFIRMATORY_NAME"}
