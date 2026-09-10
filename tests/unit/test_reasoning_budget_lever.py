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

import pytest
from pydantic import ValidationError

from app.ai.config import ERLAUBTE_DENKSTUFEN, InferenceSettings
from app.ai.runtime import _mit_denkaufwand, _mit_denkbudget


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


# ---------------------------------------------------------------------------
# Der zweite Dialekt — und der Grund, warum es ihn braucht.
# ---------------------------------------------------------------------------
#
# Am 2026-09-09 wurde auf kai-pi5 die Route auf `gemini/gemini-3.6-flash`
# umgestellt. Dabei kehrte sich die Messung von 2026-09-08 um. Ueber den
# laufenden Proxy gemessen, derselbe Prompt:
#
#     ohne Parameter               876 Denk-Token, 8353 ms
#     thinking.budget_tokens=0     867 Denk-Token, 6526 ms   <- wirkungslos
#     reasoning_effort="low"       499 Denk-Token, 6366 ms
#     reasoning_effort="minimal"     0 Denk-Token, 1301 ms
#
# Der Beleg fuer "wirkungslos" ist nicht der Token-Zaehler -- der schwankt --,
# sondern der Statuscode: derselbe Wert `0` gibt DIREKT gegen Google HTTP 400
# ("invalid argument"; `gemini-3.6-flash` kann Denken nicht abschalten), ueber
# LiteLLM 1.99.0 aber HTTP 200 mit unveraendertem Denkaufwand. Ein Parameter,
# der eine Ablehnung ausloesen MUESSTE und keine ausloest, ist nie angekommen.


def test_ohne_stufe_bleibt_die_nutzlast_unberuehrt() -> None:
    """Auch hier gilt: kein Eintrag, kein Eingriff."""
    payload = {"messages": [{"role": "user", "content": "hi"}]}

    ergebnis = _mit_denkaufwand(payload, _settings(), "bulk")

    assert ergebnis is payload, "nicht einmal kopiert"


def test_die_stufe_der_route_landet_in_der_nutzlast() -> None:
    ergebnis = _mit_denkaufwand({}, _settings(route_reasoning_effort={"bulk": "minimal"}), "bulk")

    assert ergebnis == {"reasoning_effort": "minimal"}


def test_jede_route_traegt_ihre_eigene_stufe() -> None:
    """Der Sinn des Reglers: teuer denken, wo es zaehlt — sonst nicht."""
    einstellungen = _settings(route_reasoning_effort={"bulk": "minimal", "critical": "high"})

    sparsam = _mit_denkaufwand({}, einstellungen, "bulk")
    grosszuegig = _mit_denkaufwand({}, einstellungen, "critical")
    unberuehrt = _mit_denkaufwand({}, einstellungen, "standard")

    assert sparsam == {"reasoning_effort": "minimal"}
    assert grosszuegig == {"reasoning_effort": "high"}
    assert unberuehrt == {}


def test_die_angabe_des_aufrufers_bleibt_stehen() -> None:
    """Er weiss mehr ueber seinen Fall als eine Routen-Vorgabe."""
    payload = {"reasoning_effort": "high"}

    ergebnis = _mit_denkaufwand(
        payload, _settings(route_reasoning_effort={"bulk": "minimal"}), "bulk"
    )

    assert ergebnis == {"reasoning_effort": "high"}


def test_die_stufe_veraendert_die_nutzlast_des_aufrufers_nicht() -> None:
    """Kopieren statt schreiben — der Aufrufer besitzt sein Objekt."""
    payload: dict[str, Any] = {"messages": []}

    _mit_denkaufwand(payload, _settings(route_reasoning_effort={"bulk": "low"}), "bulk")

    assert payload == {"messages": []}


def test_beide_regler_koennen_nebeneinander_stehen() -> None:
    """Zwei Dialekte, keine zwei Meinungen.

    Ein Transport, der `thinking` nativ traegt, liest das Budget; Gemini liest
    die Stufe. Eine Umrechnung zwischen beiden waere geraten — `minimal` ist
    kein bestimmter Token-Wert.
    """
    einstellungen = _settings(
        route_reasoning_budget={"bulk": 0},
        route_reasoning_effort={"bulk": "minimal"},
    )

    ergebnis = _mit_denkaufwand(_mit_denkbudget({}, einstellungen, "bulk"), einstellungen, "bulk")

    assert ergebnis == {
        "thinking": {"type": "enabled", "budget_tokens": 0},
        "reasoning_effort": "minimal",
    }


def test_eine_unbekannte_stufe_wird_abgewiesen() -> None:
    """Ein Tippfehler wuerde sonst still zu "kein Regler".

    Genau die Klasse Fehler, gegen die dieses Feld entstanden ist: ein
    Parameter, den niemand annimmt, und eine Rechnung, die trotzdem laeuft.
    """
    with pytest.raises(ValidationError):
        _settings(route_reasoning_effort={"bulk": "minimum"})


def test_die_erlaubten_stufen_sind_die_von_litellm() -> None:
    assert ERLAUBTE_DENKSTUFEN == {"minimal", "low", "medium", "high"}
