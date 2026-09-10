"""Ein erschoepftes Budget darf nicht jede Aufrufklasse gleichzeitig stilllegen.

Der reale Fall, gegen den diese Datei gebaut ist (09. und 10.09.2026, an zwei
Tagen und ueber drei Release-Staende gemessen):

Nach Erreichen des Tageslimits von 1,00 USD faellt der Anteil der
LLM-Anreicherung von rund 24 % auf **0 %**. Der Zufluss laeuft unvermindert
weiter, `is_analyzed=1` bleibt bei 100 % — und die Alert-Erzeugung ist
trotzdem tot:

    09.09. nach dem Schnitt   374 Dokumente,   0 mit prio>=7
    10.09. nach dem Schnitt    43 Dokumente,   0 mit prio>=7

Der Grund ist strukturell und nicht statistisch: der Regelpfad erreicht ueber
44.199 Dokumente maximal Prioritaet **6**, die Alert-Schwelle ist **7**. Keines
der 11.878 Dokumente mit prio>=7 stammt aus dem Regelpfad. Nach dem Limit KANN
das System also keinen Alert mehr erzeugen, unabhaengig von der Weltlage.

Ursache im Code: `decide()` kannte nur EINEN Topf. Ein Aufruf, der einen Alert
ausloesen koennte, und einer, der ein Randdokument einordnet, wurden identisch
behandelt — und fielen gemeinsam aus.

Diese Datei haelt die Trennung fest. Sie prueft NICHT, ob eine bestimmte Zahl
richtig gewaehlt ist; sie prueft, dass eine Reserve das tut, was eine Reserve
tun muss: fuer ihre Klasse verfuegbar bleiben, wenn der allgemeine Topf leer
ist, und fuer die anderen Klassen unantastbar sein.
"""

from __future__ import annotations

import pytest

from app.ai.budget import BudgetPolicy, BudgetState, decide


# Ein Fenster mit vollstaendig belegten Kosten — sonst antwortet `decide()`
# `allow_unbudgeted`, und der Test misst die Buchhaltung statt der Reserve.
def _zustand(gebucht: float, aufrufe: int = 10) -> BudgetState:
    return BudgetState(booked_usd=gebucht, known_calls=aufrufe, unknown_calls=0)


_LEER = BudgetState(booked_usd=0.0, known_calls=0, unknown_calls=0)


def _entscheide(gebucht: float, klasse: str, policy: BudgetPolicy) -> str:
    return decide(
        daily=_zustand(gebucht),
        monthly=_LEER,
        policy=policy,
        estimated_request_cost_usd=0.01,
        budget_class=klasse,
    )


_POLICY = BudgetPolicy(
    daily_limit_usd=1.00,
    alert_reserve_usd=0.15,
    validation_reserve_usd=0.05,
)


def test_ohne_reserven_bleibt_alles_wie_bisher() -> None:
    """Die Voreinstellung darf kein Verhalten aendern.

    Reserven sind eine Entscheidung des Operators. Ohne Eintrag verhaelt sich
    `decide()` exakt wie vorher — sonst waere die Umstellung eine stille
    Aenderung der Kostenlage.
    """
    ohne = BudgetPolicy(daily_limit_usd=1.00)

    for klasse in ("normal", "critical_alert", "shadow_validation"):
        assert _entscheide(0.50, klasse, ohne) == "allow"
        assert _entscheide(1.00, klasse, ohne) == "reject"


def test_der_normalpfad_stoppt_frueher_und_laesst_die_reserven_stehen() -> None:
    """Das ist der ganze Mechanismus.

    Bei 1,00 USD Limit, 0,15 Alert- und 0,05 Validierungsreserve darf der
    Normalpfad nur bis 0,80 USD laufen. Was darueber liegt, gehoert den
    anderen — sonst gaebe es die Reserve nur auf dem Papier.
    """
    assert _entscheide(0.79, "normal", _POLICY) == "allow"
    assert _entscheide(0.80, "normal", _POLICY) == "reject"


def test_der_alertpfad_laeuft_weiter_wenn_der_normalpfad_schon_steht() -> None:
    """Genau der Fall vom 09. und 10.09. — und genau der, der scheiterte."""
    assert _entscheide(0.80, "normal", _POLICY) == "reject"
    assert _entscheide(0.80, "critical_alert", _POLICY) == "allow"
    assert _entscheide(0.94, "critical_alert", _POLICY) == "allow"


def test_die_validierungsreserve_ueberlebt_auch_den_alertpfad() -> None:
    """Sonst frisst ein Alert-Sturm die Faehigkeit auf, ueberhaupt zu messen."""
    assert _entscheide(0.95, "critical_alert", _POLICY) == "reject"
    assert _entscheide(0.95, "shadow_validation", _POLICY) == "allow"
    assert _entscheide(0.99, "shadow_validation", _POLICY) == "allow"


def test_am_harten_limit_ist_fuer_jede_klasse_schluss() -> None:
    """Eine Reserve verschiebt die Grenze zwischen den Klassen, nicht das Limit.

    Das Tageslimit bleibt das Tageslimit. Waere es anders, waere die Reserve
    eine stille Erhoehung — und genau das hat der Operator ausgeschlossen.
    """
    for klasse in ("normal", "critical_alert", "shadow_validation"):
        assert _entscheide(1.00, klasse, _POLICY) == "reject"
        assert _entscheide(1.50, klasse, _POLICY) == "reject"


def test_reserven_koennen_das_limit_nicht_uebersteigen() -> None:
    """Sonst waere der Normalpfad ab dem ersten Aufruf gesperrt.

    Eine Konfiguration, deren Reserven groesser sind als das Limit, ist keine
    Politik, sondern ein Tippfehler — und sie soll beim Bau auffallen und
    nicht im Betrieb.
    """
    with pytest.raises(ValueError, match="Reserven"):
        BudgetPolicy(daily_limit_usd=0.10, alert_reserve_usd=0.15)


def test_eine_unbekannte_klasse_wird_abgewiesen() -> None:
    """Ein Tippfehler in der Klasse duerfte sonst still zum Normalpfad werden.

    Die Klasse entscheidet, welche Reserve gilt. Sie zu raten hiesse, im
    Zweifel die knappste zu nehmen — oder schlimmer, die grosszuegigste.
    """
    with pytest.raises(ValueError, match="Budgetklasse"):
        _entscheide(0.50, "kritisch", _POLICY)


def test_ohne_limit_greift_keine_reserve() -> None:
    """Wo nichts begrenzt ist, gibt es nichts zu reservieren."""
    unbegrenzt = BudgetPolicy(alert_reserve_usd=0.15, validation_reserve_usd=0.05)

    for klasse in ("normal", "critical_alert", "shadow_validation"):
        assert _entscheide(99.0, klasse, unbegrenzt) == "allow"


def test_die_schaetzung_wird_gegen_die_klassengrenze_geprueft() -> None:
    """Sonst reisst der letzte Aufruf die Reserve der anderen auf.

    `decide()` lehnt schon heute ab, wenn eine Schaetzung ueber das Limit
    fuehrt. Dieselbe Vorausschau muss fuer die Klassengrenze gelten.
    """
    ergebnis = decide(
        daily=_zustand(0.78),
        monthly=_LEER,
        policy=_POLICY,
        estimated_request_cost_usd=0.05,  # 0,78 + 0,05 = 0,83 > 0,80
        budget_class="normal",
    )

    assert ergebnis == "reject"


def test_die_klasse_erreicht_den_gateway_und_wirkt_dort() -> None:
    """Der Helfer allein beweist nichts — er muss verdrahtet sein.

    Diese Kette hat heute mehrfach gezeigt, dass eine richtige Funktion und
    ein richtiger Aufruf zwei verschiedene Dinge sind. Deshalb wird hier das
    ERGEBNIS des Gateways gelesen und nicht `decide()` noch einmal befragt.
    """
    from app.ai.gateway import execute

    gebucht = _zustand(0.85)  # ueber dem Normaldeckel 0,80, unter dem Alertdeckel 0,95

    normal = execute(
        purpose="analysis",
        alias="kai-standard",
        direct_call=None,  # kein Transport noetig: geprueft wird das Budget-Urteil
        ceiling="primary",
        budget_policy=_POLICY,
        daily=gebucht,
        monthly=_LEER,
        estimated_request_cost_usd=0.01,
    )
    alert = execute(
        purpose="analysis",
        alias="kai-standard",
        direct_call=None,  # kein Transport noetig: geprueft wird das Budget-Urteil
        ceiling="primary",
        budget_policy=_POLICY,
        daily=gebucht,
        monthly=_LEER,
        estimated_request_cost_usd=0.01,
        budget_class="critical_alert",
    )

    assert normal.budget == "reject", "der Massenpfad muesste hier stehen"
    assert alert.budget != "reject", "der Alertpfad wurde mitgesperrt — die Reserve wirkt nicht"


def test_ohne_angabe_bleibt_der_gateway_beim_massenpfad() -> None:
    """Gegenprobe: die Vorgabe darf nicht versehentlich grosszuegig sein."""
    from app.ai.gateway import execute

    ergebnis = execute(
        purpose="analysis",
        alias="kai-standard",
        direct_call=None,  # kein Transport noetig: geprueft wird das Budget-Urteil
        ceiling="primary",
        budget_policy=_POLICY,
        daily=_zustand(0.85),
        monthly=_LEER,
        estimated_request_cost_usd=0.01,
    )

    assert ergebnis.budget == "reject"
