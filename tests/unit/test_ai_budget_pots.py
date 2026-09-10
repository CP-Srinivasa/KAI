"""Ein erschöpftes Budget darf das Alerting nicht abschalten — nur bremsen.

Der Befund vom 2026-09-10, gegen den diese Datei geschrieben ist: nach
Erreichen des Tageslimits fiel der Anteil echter LLM-Analyse von 24,8 % auf
0,3 %, der Dokumentzufluss lief unvermindert weiter, und ``is_analyzed`` blieb
bei rund 100 % — das Dashboard blieb grün. Die Alerts nicht: 23 von 202
Dokumenten VOR dem Limit, **0 von 373 danach**.

Das ist kein Mengenproblem, sondern ein struktureller Nullpunkt. Der Regelpfad
erreicht über 44.018 Dokumente maximal Priorität 6,0; die Alert-Schwelle liegt
bei 7. Sobald das Budget die echte Analyse abschaltet, ist die Schwelle nicht
mehr schwer erreichbar, sondern unerreichbar — und weil der Regelpfad brav
weiterschreibt, sieht der Betrieb gesund aus.

Zwei Eigenschaften müssen deshalb gleichzeitig gelten, und die Tests hier
halten beide fest:

* Die Reserve ist **kein zweiter Topf zum Ausgeben**, sondern der letzte
  Ausweg. Wäre sie eine parallele Spur, hätten gewöhnliche Dokumente sie am
  Vormittag geleert.
* Die Reserve ist **begrenzt, auch wenn niemand mehr rechnen kann**. Genau der
  Zustand ``COST_UNKNOWN``, der in v1 alles sperrt, darf sie nicht wieder auf
  null bringen — sonst wäre ein Messproblem erneut ein vollständiger
  Alert-Ausfall.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.ai.budget import (
    LEGACY_POT,
    POTS,
    BudgetPolicy,
    BudgetState,
    ReservePolicy,
    decide_pot,
)

#: Zwei echte Fälle vom 2026-09-10 kosteten 0,00536 und 0,00515 USD. Ein
#: halber Cent je vollständiger Analyse ist die Grössenordnung, in der die
#: Zahlen hier gewählt sind — damit die Tests von derselben Wirklichkeit
#: reden wie die Voreinstellungen.
FALLKOSTEN = 0.005

TAGESLIMIT = BudgetPolicy(daily_limit_usd=1.25)
RESERVEN = ReservePolicy(
    alert_reserve_usd=0.15,
    alert_reserve_max_calls=40,
    validation_reserve_usd=0.05,
    validation_reserve_max_calls=10,
)
#: 1.25 − 0.15 − 0.05
NORMALE_DECKE = 1.05


def zustand(usd: float, *, calls: int | None = None, unbekannt: int = 0) -> BudgetState:
    """Ein Topfzustand. ``calls`` ergibt sich sonst aus den Kosten."""
    bekannt = calls if calls is not None else max(1, int(usd / FALLKOSTEN))
    return BudgetState(booked_usd=usd, known_calls=bekannt, unknown_calls=unbekannt)


def entscheide(**kwargs: object) -> object:
    grund = {
        "route": "standard",
        "policy": TAGESLIMIT,
        "reserves": RESERVEN,
    }
    grund.update(kwargs)
    return decide_pot(**grund)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Ohne gesetzte Reserven ändert sich nichts.
# ---------------------------------------------------------------------------


def test_ohne_reserven_bleibt_alles_im_normalen_topf() -> None:
    """Das Einspielen allein darf das Verhalten nicht ändern.

    Eine Policy, die schon durch ihre Anwesenheit anders entscheidet, wäre im
    Rollout nicht von einem Defekt zu unterscheiden.
    """
    urteil = entscheide(
        reserves=ReservePolicy(),
        pots={"normal": zustand(0.5)},
    )

    assert urteil.pot == "normal"
    assert urteil.allowed


def test_ohne_reserven_sperrt_das_tageslimit_wie_bisher() -> None:
    urteil = entscheide(reserves=ReservePolicy(), pots={"normal": zustand(1.25)})

    assert urteil.allowed is False
    assert urteil.reason == "normal_budget_exhausted"


# ---------------------------------------------------------------------------
# Die Decke des normalen Topfes.
# ---------------------------------------------------------------------------


def test_gewoehnliche_arbeit_endet_vor_den_reserven() -> None:
    """Bei 1,05 USD ist Schluss, nicht bei 1,25 — sonst gäbe es nichts zu reservieren."""
    assert RESERVEN.normal_ceiling_usd(1.25) == pytest.approx(NORMALE_DECKE)

    knapp_darunter = entscheide(pots={"normal": zustand(NORMALE_DECKE - 0.01)})
    genau_darauf = entscheide(pots={"normal": zustand(NORMALE_DECKE)})

    assert knapp_darunter.allowed
    assert genau_darauf.allowed is False


def test_reserven_groesser_als_das_budget_ergeben_keine_negative_decke() -> None:
    """Eine extreme Konfiguration, aber keine unvorhersehbare.

    Eine negative Decke wäre schlimmer als eine leere: gegen sie fiele jeder
    Vergleich anders aus, je nachdem wie herum er geschrieben ist.
    """
    uebergross = ReservePolicy(alert_reserve_usd=2.0, validation_reserve_usd=1.0)

    assert uebergross.normal_ceiling_usd(1.25) == 0.0


def test_ohne_tageslimit_begrenzt_der_normale_topf_nichts() -> None:
    urteil = entscheide(policy=BudgetPolicy(), pots={"normal": zustand(99.0)})

    assert urteil.pot == "normal"
    assert urteil.allowed


# ---------------------------------------------------------------------------
# Die Alert-Reserve ist der LETZTE Ausweg, keine zweite Spur.
# ---------------------------------------------------------------------------


def test_alert_faehige_arbeit_zahlt_zuerst_aus_dem_normalen_topf() -> None:
    """Der wichtigste Test dieser Datei.

    Würde ein alert-fähiges Dokument sofort aus der Reserve bezahlt, wäre die
    Reserve am Vormittag von gewöhnlichen Dokumenten geleert, die zufällig gut
    gepunktet haben — und abends, wenn sie zählt, leer. Eine Reserve, die im
    Normalbetrieb mitläuft, ist keine.
    """
    urteil = entscheide(alert_eligible=True, pots={"normal": zustand(0.20)})

    assert urteil.pot == "normal", "solange normal trägt, zahlt normal"
    assert urteil.allowed


def test_die_reserve_greift_erst_wenn_der_normale_topf_erschoepft_ist() -> None:
    urteil = entscheide(
        alert_eligible=True,
        pots={"normal": zustand(NORMALE_DECKE), "alert_reserve": zustand(0.0, calls=0)},
    )

    assert urteil.pot == "alert_reserve"
    assert urteil.allowed


def test_gewoehnliche_arbeit_kommt_an_die_reserve_nicht_heran() -> None:
    """Ohne diesen Satz wäre die Reserve nur eine Erhöhung des Tagesbudgets."""
    urteil = entscheide(
        alert_eligible=False,
        pots={"normal": zustand(NORMALE_DECKE), "alert_reserve": zustand(0.0, calls=0)},
    )

    assert urteil.pot == "normal"
    assert urteil.allowed is False
    assert urteil.reason == "normal_budget_exhausted"


def test_die_erschoepfte_reserve_lehnt_ab_und_nennt_sich_beim_namen() -> None:
    """Der Grund muss die Reserve benennen.

    ``normal_budget_exhausted`` an dieser Stelle würde den Operator dazu
    bringen, das Tagesbudget zu erhöhen — was nichts hilft, wenn die Reserve
    das Problem ist.
    """
    urteil = entscheide(
        alert_eligible=True,
        pots={"normal": zustand(NORMALE_DECKE), "alert_reserve": zustand(0.15)},
    )

    assert urteil.pot == "alert_reserve"
    assert urteil.allowed is False
    assert urteil.reason == "alert_reserve_exhausted"


# ---------------------------------------------------------------------------
# Fail-closed: die Reserve bleibt begrenzt, auch ohne belegte Kosten.
# ---------------------------------------------------------------------------


def test_cost_unknown_sperrt_gewoehnliche_arbeit() -> None:
    """Das v1-Verhalten bleibt: wer nicht weiss, was er ausgibt, hält an."""
    urteil = entscheide(cost_unknown=True, pots={"normal": zustand(0.01)})

    assert urteil.allowed is False
    assert urteil.reason == "cost_unknown"


def test_cost_unknown_sperrt_die_alert_reserve_nicht() -> None:
    """Ein MESSPROBLEM darf kein vollständiger Alert-Ausfall sein.

    Genau diese Verwechslung ist am 2026-09-10 einmal passiert — dort über das
    Limit, hier wäre es über die Messung. Beides sähe im Dashboard gleich
    gesund aus.
    """
    urteil = entscheide(
        cost_unknown=True,
        alert_eligible=True,
        pots={"normal": zustand(0.01), "alert_reserve": zustand(0.0, calls=0)},
    )

    assert urteil.pot == "alert_reserve"
    assert urteil.allowed


def test_die_aufrufgrenze_haelt_die_reserve_auch_ohne_kosten() -> None:
    """Der Preis dafür, dass ``COST_UNKNOWN`` sie nicht sperrt.

    Ohne diese Grenze wäre die Reserve im Zustand ``COST_UNKNOWN`` unbegrenzt:
    ``booked_usd`` bliebe 0, weil nichts belegt ist, und keine USD-Schwelle
    würde je greifen. Die Reserve wäre dann ausgerechnet in dem Zustand offen,
    in dem niemand mitzählt.
    """
    voll = BudgetState(booked_usd=0.0, known_calls=0, unknown_calls=40)
    urteil = entscheide(
        cost_unknown=True,
        alert_eligible=True,
        pots={"normal": zustand(0.01), "alert_reserve": voll},
    )

    assert urteil.allowed is False
    assert urteil.reason == "alert_reserve_exhausted"


def test_unbelegte_aufrufe_zaehlen_gegen_die_aufrufgrenze() -> None:
    """``total_calls`` und nicht ``known_calls``.

    Ein Aufruf, dessen Kosten unbekannt sind, hat die Reserve trotzdem
    benutzt. Zählte er nicht mit, wäre die Aufrufgrenze genau in dem Zustand
    wirkungslos, für den es sie gibt.
    """
    gemischt = BudgetState(booked_usd=0.02, known_calls=4, unknown_calls=36)

    urteil = entscheide(
        alert_eligible=True,
        pots={"normal": zustand(NORMALE_DECKE), "alert_reserve": gemischt},
    )

    assert urteil.allowed is False


# ---------------------------------------------------------------------------
# Der Validierungstopf ist in BEIDE Richtungen dicht.
# ---------------------------------------------------------------------------


def test_validierung_zahlt_aus_ihrem_eigenen_topf() -> None:
    urteil = entscheide(validation=True, pots={"normal": zustand(0.10)})

    assert urteil.pot == "validation"
    assert urteil.allowed


def test_validierung_laeuft_auch_bei_erschoepftem_normalen_topf() -> None:
    """Sonst wäre die Reserve keine Reserve, sondern ein Rest."""
    urteil = entscheide(
        validation=True,
        pots={"normal": zustand(NORMALE_DECKE), "validation": zustand(0.0, calls=0)},
    )

    assert urteil.allowed


def test_validierung_greift_nicht_auf_die_alert_reserve_zu() -> None:
    """Ein Testtopf, der sich aus der Produktionskapazität bedient, ist eine
    Umgehung mit freundlichem Namen."""
    urteil = entscheide(
        validation=True,
        alert_eligible=True,
        pots={"validation": zustand(0.05), "alert_reserve": zustand(0.0, calls=0)},
    )

    assert urteil.pot == "validation"
    assert urteil.allowed is False
    assert urteil.reason == "validation_reserve_exhausted"


def test_gewoehnliche_arbeit_greift_nicht_auf_den_validierungstopf_zu() -> None:
    """Die Gegenrichtung, und sie ist nicht dieselbe Aussage.

    Der Operator hat beides verlangt: kein Übertrag auf PRIMARY und keiner auf
    normale Analyse. Ein Test nur in einer Richtung liesse die andere offen.
    """
    urteil = entscheide(
        pots={"normal": zustand(NORMALE_DECKE), "validation": zustand(0.0, calls=0)},
    )

    assert urteil.pot == "normal"
    assert urteil.allowed is False


def test_die_aufrufgrenze_gilt_auch_fuer_die_validierung() -> None:
    verbraucht = BudgetState(booked_usd=0.0, known_calls=0, unknown_calls=10)
    urteil = entscheide(validation=True, pots={"validation": verbraucht})

    assert urteil.allowed is False


# ---------------------------------------------------------------------------
# `critical` bleibt, was es war.
# ---------------------------------------------------------------------------


def test_critical_bleibt_ausgenommen() -> None:
    """Diese Ausnahme stand vor v2 und wird von v2 nicht angetastet.

    Sie ist die Fernbedienung des Operators für genau den Zustand, den er
    gerade beheben muss — ein erschöpftes Budget eingeschlossen.
    """
    urteil = entscheide(route="critical", pots={"normal": zustand(99.0)})

    assert urteil.pot == "exempt"
    assert urteil.allowed


def test_critical_zahlt_nicht_aus_der_alert_reserve() -> None:
    """Sonst verbrauchte die Operator-Steuerung die Kapazität der Alerts."""
    urteil = entscheide(
        route="critical",
        alert_eligible=True,
        pots={"normal": zustand(99.0), "alert_reserve": zustand(0.15)},
    )

    assert urteil.pot == "exempt"
    assert urteil.allowed


# ---------------------------------------------------------------------------
# Getrennte Zähler: was der Strom hergibt.
# ---------------------------------------------------------------------------


def _zeile(pfad: Path, **felder: object) -> None:
    grund: dict[str, object] = {
        "ts": "2026-09-10T12:00:00+00:00",
        "provider": "gemini",
        "model": "gemini-3.6-flash",
        "cost_usd": FALLKOSTEN,
        "cost_status": "known",
        "ok": True,
    }
    grund.update(felder)
    with pfad.open("a", encoding="utf-8") as f:
        f.write(json.dumps(grund) + "\n")


@pytest.fixture
def strom(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from app.ai import spend

    pfad = tmp_path / "llm_telemetry.jsonl"
    pfad.touch()
    monkeypatch.setattr(spend, "reset_spend_cache", spend.reset_spend_cache)
    spend.reset_spend_cache()
    return pfad


def test_der_strom_trennt_die_toepfe(strom: Path) -> None:
    """Ohne getrennte Zähler wäre jede Reserve eine Behauptung."""
    from app.ai.spend import reset_spend_cache, spend_window

    for _ in range(3):
        _zeile(strom, budget_pot="normal")
    _zeile(strom, budget_pot="alert_reserve")
    _zeile(strom, budget_pot="validation")
    reset_spend_cache()

    toepfe = spend_window("today", path=strom, now=_jetzt()).pot_states()

    assert toepfe["normal"].booked_usd == pytest.approx(3 * FALLKOSTEN)
    assert toepfe["alert_reserve"].booked_usd == pytest.approx(FALLKOSTEN)
    assert toepfe["validation"].booked_usd == pytest.approx(FALLKOSTEN)


def test_jeder_bekannte_topf_kommt_vor_auch_der_leere(strom: Path) -> None:
    """Ein fehlender Schlüssel zwänge jeden Aufrufer zu einem eigenen
    Standardwert — und einer von ihnen nähme irgendwann einen anderen."""
    from app.ai.spend import reset_spend_cache, spend_window

    _zeile(strom, budget_pot="normal")
    reset_spend_cache()

    toepfe = spend_window("today", path=strom, now=_jetzt()).pot_states()

    assert set(toepfe) == set(POTS)


def test_altzeilen_ohne_topf_fuellen_den_normalen_topf(strom: Path) -> None:
    """Nicht die Reserven, und auch nicht das Nichts.

    Altverbrauch als reservefrei zu behandeln hiesse, am ersten Tag nach dem
    Rollout mit einem geschenkten Guthaben zu starten, das nie ausgegeben
    wurde.
    """
    from app.ai.spend import reset_spend_cache, spend_window

    _zeile(strom)
    reset_spend_cache()

    toepfe = spend_window("today", path=strom, now=_jetzt()).pot_states()

    assert toepfe[LEGACY_POT].booked_usd == pytest.approx(FALLKOSTEN)
    assert toepfe["alert_reserve"].total_calls == 0
    assert toepfe["validation"].total_calls == 0


def test_ein_unbekannter_topfname_verschwindet_nicht(strom: Path) -> None:
    """Verbrauch, der in keiner Reserve auftaucht und in keiner Summe fehlt,
    wäre unsichtbar — die teuerste Sorte Buchungsfehler."""
    from app.ai.spend import reset_spend_cache, spend_window

    _zeile(strom, budget_pot="tippfehler")
    reset_spend_cache()

    fenster = spend_window("today", path=strom, now=_jetzt())
    toepfe = fenster.pot_states()

    assert toepfe[LEGACY_POT].booked_usd == pytest.approx(FALLKOSTEN)
    assert sum(t.total_calls for t in toepfe.values()) == fenster.calls


def test_die_summe_der_toepfe_ist_der_tagesverbrauch(strom: Path) -> None:
    """Die Probe aufs Exempel: getrennte Zähler dürfen nichts erfinden und
    nichts verlieren."""
    from app.ai.spend import reset_spend_cache, spend_window

    _zeile(strom, budget_pot="normal")
    _zeile(strom, budget_pot="alert_reserve")
    _zeile(strom, budget_pot="validation")
    _zeile(strom, budget_pot="exempt")
    reset_spend_cache()

    fenster = spend_window("today", path=strom, now=_jetzt())
    toepfe = fenster.pot_states()

    assert sum(t.booked_usd for t in toepfe.values()) == pytest.approx(fenster.known_cost_usd)


def _jetzt():
    from datetime import UTC, datetime

    return datetime(2026, 9, 10, 18, 0, tzinfo=UTC)
