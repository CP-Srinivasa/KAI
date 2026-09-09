"""Das Denkbudget als Regler — der teuerste Posten ist der Weg zur Antwort.

Am 2026-09-08 auf kai-pi5 gemessen, `gemini/gemini-2.5-flash`, derselbe Prompt,
fünf Varianten:

===========================  =========  ====  ===========  =======
Variante                     reasoning  text  Kosten USD   Latenz
===========================  =========  ====  ===========  =======
ohne Parameter                     381    15    0,0009957  3632 ms
``reasoning_effort=minimal``         –    40    0,0001057   681 ms
``reasoning_effort=low``           380    16    0,0009957  4565 ms
``thinking budget 0``                –    42    0,0001107   772 ms
``thinking budget 128``            104    45    0,0003782  2354 ms
===========================  =========  ====  ===========  =======

Zwei Dinge stehen darin. Erstens: Denken abschalten kostet ein Neuntel und
läuft fünfmal schneller, und die Antwort wurde dabei **länger**. Ob sie besser
war, sagt die Messung nicht — deshalb ist es ein Regler und keine Vorgabe.

Zweitens, und das ist der Grund für die Bauform: ``reasoning_effort="low"``
wurde durchgereicht und blieb wirkungslos. Wer es setzte, glaubte zu sparen und
sparte nichts. Ein Token-Budget ist nachprüfbar, eine Stufe ist ein Versprechen.
"""

from __future__ import annotations

from typing import Any

from app.ai.config import InferenceSettings
from app.ai.runtime import _mit_denkbudget


def _settings(**kwargs: Any) -> InferenceSettings:
    return InferenceSettings(enabled=True, mode_ceiling="shadow", **kwargs)


def test_ohne_eintrag_bleibt_die_nutzlast_unberuehrt() -> None:
    """Kein Regler ist auch eine Stellung — die des Modells.

    Wer nichts einträgt, bekommt das Verhalten, das er heute hat. Ein
    stillschweigender Default hätte die Kosten still verändert.
    """
    payload = {"messages": [{"role": "user", "content": "hi"}]}

    ergebnis = _mit_denkbudget(payload, _settings(), "bulk")

    assert ergebnis is payload, "nicht einmal kopiert"


def test_null_ist_ein_gueltiger_wert_und_der_wirksamste() -> None:
    """`0` schaltet das Denken ab und bringt den Faktor 9.

    Eine Prüfung auf Wahrheitswert statt auf ``None`` hätte ausgerechnet die
    Einstellung verschluckt, die am meisten spart — der Fehler wäre grün
    durchgelaufen und hätte nur die Rechnung erhöht.
    """
    ergebnis = _mit_denkbudget({}, _settings(route_reasoning_budget={"bulk": 0}), "bulk")

    assert ergebnis is not None
    assert ergebnis["thinking"] == {"type": "enabled", "budget_tokens": 0}


def test_das_budget_gilt_je_route() -> None:
    """Bulk darf sparsam denken, wo Critical es sich leisten soll."""
    einstellungen = _settings(route_reasoning_budget={"bulk": 0, "critical": 1024})

    sparsam = _mit_denkbudget({}, einstellungen, "bulk")
    grosszuegig = _mit_denkbudget({}, einstellungen, "critical")
    unberuehrt = _mit_denkbudget({}, einstellungen, "standard")

    assert sparsam is not None and sparsam["thinking"]["budget_tokens"] == 0
    assert grosszuegig is not None and grosszuegig["thinking"]["budget_tokens"] == 1024
    assert unberuehrt == {}, "eine Route ohne Eintrag bleibt, wie sie war"


def test_die_angabe_des_aufrufers_wird_nicht_ueberschrieben() -> None:
    """Er weiß mehr über seinen Fall als eine Routen-Vorgabe.

    Ein stilles Überschreiben wäre eine zweite Autorität über dieselbe Zahl —
    und der Aufrufer sähe im Code etwas, das zur Laufzeit nicht gilt.
    """
    payload = {"thinking": {"type": "enabled", "budget_tokens": 512}}

    ergebnis = _mit_denkbudget(payload, _settings(route_reasoning_budget={"bulk": 0}), "bulk")

    assert ergebnis is payload
    assert ergebnis["thinking"]["budget_tokens"] == 512


def test_die_nutzlast_des_aufrufers_wird_nicht_veraendert() -> None:
    """Die Ergänzung geht in eine Kopie.

    Ein Aufrufer, der seine Nutzlast wiederverwendet, bekäme sonst beim zweiten
    Aufruf eine andere als beim ersten — und die Route stünde plötzlich in
    einem Objekt, das ihm gehört.
    """
    payload: dict[str, Any] = {"messages": []}

    _mit_denkbudget(payload, _settings(route_reasoning_budget={"bulk": 64}), "bulk")

    assert payload == {"messages": []}


def test_ein_negatives_budget_wird_zu_null_und_nicht_durchgereicht() -> None:
    """Ein Tippfehler darf keine Anfrage erzeugen, die der Upstream ablehnt.

    Fail-closed in die günstige Richtung: weniger denken ist immer erlaubt.
    """
    ergebnis = _mit_denkbudget({}, _settings(route_reasoning_budget={"bulk": -5}), "bulk")

    assert ergebnis is not None
    assert ergebnis["thinking"]["budget_tokens"] == 0


def test_ohne_nutzlast_entsteht_eine() -> None:
    """`payload=None` ist der Normalfall für Aufrufer, die nur den Parser setzen."""
    ergebnis = _mit_denkbudget(None, _settings(route_reasoning_budget={"bulk": 128}), "bulk")

    assert ergebnis == {"thinking": {"type": "enabled", "budget_tokens": 128}}
