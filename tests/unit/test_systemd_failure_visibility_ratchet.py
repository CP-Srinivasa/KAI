"""Ratchet: eine neue Unit darf ihren Fehler nicht wieder unsichtbar machen.

Befund 2026-08-17: 17 von 59 Units trugen ``ExecStart=-`` (Exit-Code maskiert,
Unit gilt als ``success``) und **null** trugen ``OnFailure=``. Beides zusammen
heißt: ein gescheiterter Job ist von einem gesunden nicht unterscheidbar und
erreicht niemanden.

Diese Tests frieren den reparierten Zustand ein. Sie prüfen den WIRKSAMEN
Direktiven-Text, nicht den Dateitext — ein erklärender Kommentar wie
"BEWUSST OHNE OnFailure=" darf einen Test nie erfüllen (Lehre 2026: Textsuche
trifft den Kommentar statt der Direktive).
"""

from __future__ import annotations

from pathlib import Path

import pytest

_UNIT_DIR = Path(__file__).resolve().parents[2] / "deploy" / "systemd"
# Der Notifier selbst bekommt bewusst KEIN OnFailure: ein Alarm, der sich
# selbst alarmiert, kaskadiert.
_NOTIFIER = "kai-unit-failure-notify@.service"


def _directives(path: Path) -> list[str]:
    """Wirksame Zeilen: ohne Kommentare, ohne Leerzeilen."""
    out = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        out.append(line)
    return out


def _service_units() -> list[Path]:
    return sorted(p for p in _UNIT_DIR.glob("*.service"))


def test_there_are_units_to_check() -> None:
    """Guard: ein leeres Glob würde alle folgenden Tests trivial grün färben."""
    assert len(_service_units()) >= 50


@pytest.mark.parametrize("unit", _service_units(), ids=lambda p: p.name)
def test_no_unit_masks_its_exit_code(unit: Path) -> None:
    """``ExecStart=-`` lässt systemd einen Fehlschlag als Erfolg verbuchen."""
    offending = [d for d in _directives(unit) if d.startswith("ExecStart=-")]

    assert not offending, (
        f"{unit.name} maskiert seinen Exit-Code mit 'ExecStart=-'. "
        "Ein Fehlschlag wäre damit unsichtbar."
    )


@pytest.mark.parametrize("unit", _service_units(), ids=lambda p: p.name)
def test_every_unit_routes_its_failure_to_the_operator(unit: Path) -> None:
    if unit.name == _NOTIFIER:
        pytest.skip("Notifier alarmiert bewusst nicht sich selbst (Kaskadenschutz)")

    directives = _directives(unit)
    on_failure = [d for d in directives if d.startswith("OnFailure=")]

    assert on_failure, f"{unit.name} hat kein OnFailure= — ein Fehlschlag erreicht niemanden."
    assert any("kai-unit-failure-notify@" in d for d in on_failure), (
        f"{unit.name} hat ein OnFailure=, das nicht auf den Operator-Notifier zeigt: {on_failure}"
    )


def test_notifier_does_not_alarm_itself() -> None:
    """Kaskadenschutz ist eine Eigenschaft, kein Zufall — also festgenagelt."""
    notifier = _UNIT_DIR / _NOTIFIER
    assert notifier.exists(), "Die Notifier-Template-Unit fehlt."

    assert not [d for d in _directives(notifier) if d.startswith("OnFailure=")]
