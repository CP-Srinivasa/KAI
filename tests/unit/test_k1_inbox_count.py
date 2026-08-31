"""K1 `00c75a76a2b0e78b` — die Zählung ist Code, nicht Lesen einer Tabelle.

Der Evaluator entsteht **vor** den Daten. Das ist keine Formalie: die letzte
offene Prä-Registrierung ist genau die, bei der eine beim Zählen entstehende
Regel am teuersten wäre ([[feedback_prereg_evaluator_must_be_committed]]).

Der versiegelte Text lautet:

    >=5 qualified inbound inquiries (written, each naming a concrete signal
    provider to audit OR a clear payment-willingness signal) within 30 days of
    publishing the anonymized K1 pilot report. Fewer than 5 means KILL this
    offering. NOT counted: spam, signal-provider self-promotion without an
    audit request, internal inquiries.

Drei Dinge, die dort NICHT stehen und die dieser Zähler deshalb NICHT tut:

* er filtert **nicht** auf einen Kanal (der Seal sagt „inbound inquiries",
  nicht „E-Mail"),
* er verlangt **nicht** ``unsolicited`` — das Wort steht ausschliesslich in der
  FOLGEklausel nach FAIL und ist keine Einschlussregel,
* er dedupliziert **nicht** auf Personen — gezählt werden ``inquiries``.

Und einen vierten: es gibt **keinen INCONCLUSIVE-Zweig**. Eine kleine
Population ergibt nach dem Wortlaut FAIL, nicht „nicht auswertbar".
"""

from __future__ import annotations

import pytest

from app.research.k1_inbox_count import (
    SEAL_THRESHOLD,
    WINDOW_CANDIDATES,
    K1CountError,
    count_rows,
    parse_rows,
)

_HDR = "# datum_utc | richtung | absenderklasse | betreff | antwort | thread_id | qualifiziert"


def _line(
    ts: str,
    richtung: str = "in",
    klasse: str = "extern_menschlich",
    betreff: str = "Anfrage",
    antwort: str = "nein",
    thread: str = "T01",
    qualifiziert: str = "ja",
) -> str:
    return f"{ts} | {richtung} | {klasse} | {betreff} | {antwort} | {thread} | {qualifiziert}"


# -- Parser: fail-closed, nie stillschweigend --------------------------------


def test_kommentare_und_leerzeilen_werden_ignoriert() -> None:
    rows = parse_rows("\n".join([_HDR, "", _line("2026-07-10T10:00:00Z"), "   "]))
    assert len(rows) == 1


def test_eine_unbekannte_absenderklasse_ist_ein_fehler_kein_ueberspringen() -> None:
    """Was der Zähler nicht kennt, darf er nicht als 'zählt nicht' verbuchen."""
    with pytest.raises(K1CountError, match="absenderklasse"):
        parse_rows(_line("2026-07-10T10:00:00Z", klasse="vielleicht_kunde"))


def test_eine_kaputte_zeile_ist_ein_fehler() -> None:
    with pytest.raises(K1CountError, match="Spalten"):
        parse_rows("2026-07-10T10:00:00Z | in | extern_menschlich")


def test_ein_unlesbares_datum_ist_ein_fehler() -> None:
    with pytest.raises(K1CountError, match="datum_utc"):
        parse_rows(_line("irgendwann im Juli"))


def test_ein_unbekanntes_ja_nein_ist_ein_fehler() -> None:
    with pytest.raises(K1CountError, match="qualifiziert"):
        parse_rows(_line("2026-07-10T10:00:00Z", qualifiziert="vielleicht"))


# -- Zählung nach dem versiegelten Wortlaut ----------------------------------


def test_das_gate_zaehlt_anfragen_nicht_personen() -> None:
    """Drei qualifizierte Anfragen aus EINEM Thread sind drei, nicht eine.

    Eine Dedup-Regel wurde nicht versiegelt. Beide Zahlen werden ausgewiesen,
    aber nur die versiegelte Einheit treibt das Verdikt.
    """
    text = "\n".join(_line(f"2026-07-1{i}T10:00:00Z", thread="T01") for i in range(3))
    report = count_rows(parse_rows(text))
    window = report["windows"]["2026-07-04"]

    assert window["QUALIFIED_INBOUND_INQUIRIES"] == 3
    assert window["DISTINCT_QUALIFIED_THREADS"] == 1
    assert window["SEALED_COUNT"] == 3


def test_antworten_auf_eigene_ansprache_zaehlen_mit() -> None:
    """Der Seal schliesst sie NICHT aus — sie separat auszuweisen ist Diagnostik."""
    text = "\n".join(
        [
            _line("2026-07-10T10:00:00Z", antwort="ja", thread="T01"),
            _line("2026-07-11T10:00:00Z", antwort="nein", thread="T02"),
        ]
    )
    window = count_rows(parse_rows(text))["windows"]["2026-07-04"]

    assert window["QUALIFIED_INBOUND_INQUIRIES"] == 2
    assert window["QUALIFIED_RESPONSES_TO_OWN_OUTREACH"] == 1
    assert window["QUALIFIED_UNSOLICITED_INQUIRIES"] == 1
    assert window["SEALED_COUNT"] == 2


def test_ausgehende_nachrichten_zaehlen_nie() -> None:
    text = _line("2026-07-10T10:00:00Z", richtung="out", klasse="operator")
    window = count_rows(parse_rows(text))["windows"]["2026-07-04"]
    assert window["INBOUND_MESSAGES_TOTAL"] == 0
    assert window["SEALED_COUNT"] == 0


@pytest.mark.parametrize("klasse", ["spam", "signalanbieter_selbstpr", "intern_operator"])
def test_die_drei_seal_ausschluesse_greifen_auch_gegen_ein_qualifiziert_ja(klasse: str) -> None:
    """Der Seal zählt sie ausdrücklich NICHT — eine Markierung hebt das nicht auf."""
    window_report = count_rows(parse_rows(_line("2026-07-10T10:00:00Z", klasse=klasse)))
    window = window_report["windows"]["2026-07-04"]

    assert window["SEALED_COUNT"] == 0
    # Nicht still verschluckt: der Widerspruch wird benannt.
    assert window_report["conflicts"], "ein ausgeschlossenes 'qualifiziert=ja' muss auffallen"


def test_newsletter_und_systemmails_erfuellen_die_einschlussregel_nicht() -> None:
    text = "\n".join(
        [
            _line("2026-07-10T10:00:00Z", klasse="newsletter", qualifiziert="nein"),
            _line("2026-07-11T10:00:00Z", klasse="system_notification", qualifiziert="nein"),
            _line("2026-07-12T10:00:00Z", klasse="extern_menschlich", qualifiziert="ja"),
        ]
    )
    window = count_rows(parse_rows(text))["windows"]["2026-07-04"]

    assert window["INBOUND_MESSAGES_TOTAL"] == 3
    assert window["INBOUND_HUMAN_INQUIRIES"] == 1
    assert window["SEALED_COUNT"] == 1


# -- Die beiden Seal-Fenster -------------------------------------------------


def test_beide_fenster_werden_getrennt_gerechnet() -> None:
    """33 Tage sind kein Seal-Fenster. Der Seal gibt 30 her — in zwei Lesarten."""
    assert set(WINDOW_CANDIDATES) == {"2026-07-02", "2026-07-04"}

    text = "\n".join(
        [
            _line("2026-07-03T10:00:00Z", thread="T01"),  # nur im fruehen Fenster
            _line("2026-07-10T10:00:00Z", thread="T02"),  # in beiden
            _line("2026-08-02T10:00:00Z", thread="T03"),  # nur im spaeten Fenster
        ]
    )
    report = count_rows(parse_rows(text))

    assert report["windows"]["2026-07-02"]["SEALED_COUNT"] == 2
    assert report["windows"]["2026-07-04"]["SEALED_COUNT"] == 2
    assert report["windows_agree_on_verdict"] is True


def test_ein_verdikt_unterschied_zwischen_den_fenstern_wird_gemeldet() -> None:
    """Genau dann — und nur dann — ist die Fensterlesart eine Entscheidung."""
    early = [_line(f"2026-07-03T1{i}:00:00Z", thread=f"E{i}") for i in range(5)]
    report = count_rows(parse_rows("\n".join(early)))

    assert report["windows"]["2026-07-02"]["VERDICT"] == "MET"
    assert report["windows"]["2026-07-04"]["VERDICT"] == "NOT_MET"
    assert report["windows_agree_on_verdict"] is False


def test_ausserhalb_beider_fenster_zaehlt_nichts() -> None:
    report = count_rows(parse_rows(_line("2026-08-20T10:00:00Z")))
    assert report["windows"]["2026-07-02"]["SEALED_COUNT"] == 0
    assert report["windows"]["2026-07-04"]["SEALED_COUNT"] == 0
    assert report["out_of_both_windows"] == 1


# -- Verdikt: zwei Zweige, kein dritter --------------------------------------


def test_die_schwelle_ist_die_versiegelte() -> None:
    assert SEAL_THRESHOLD == 5


def test_vier_ist_fail_und_fuenf_ist_met() -> None:
    four = "\n".join(_line(f"2026-07-1{i}T10:00:00Z", thread=f"T{i}") for i in range(4))
    five = "\n".join(_line(f"2026-07-1{i}T10:00:00Z", thread=f"T{i}") for i in range(5))

    assert count_rows(parse_rows(four))["windows"]["2026-07-04"]["VERDICT"] == "NOT_MET"
    assert count_rows(parse_rows(five))["windows"]["2026-07-04"]["VERDICT"] == "MET"


def test_eine_leere_population_ist_fail_nicht_inconclusive() -> None:
    """Der Seal kennt keinen dritten Ausgang — auch bei null Zeilen nicht."""
    report = count_rows(parse_rows(""))
    window = report["windows"]["2026-07-04"]

    assert window["SEALED_COUNT"] == 0
    assert window["VERDICT"] == "NOT_MET"
    # Kein Verdikt-Feld darf je einen dritten Ausgang tragen. Der Vermerk in
    # ``seal_notes`` DARF das Wort nennen — er sagt ja gerade, dass es den
    # Zweig nicht gibt; eine blosse Wortsuche ueber den ganzen Report haette
    # genau diese Erklaerung verboten.
    assert {w["VERDICT"] for w in report["windows"].values()} <= {"MET", "NOT_MET"}


def test_das_verdikt_haengt_nie_an_den_distinct_contacts() -> None:
    """Fünf qualifizierte Anfragen aus einem Thread sind MET — so steht es im Seal."""
    text = "\n".join(_line(f"2026-07-1{i}T10:00:00Z", thread="T01") for i in range(5))
    window = count_rows(parse_rows(text))["windows"]["2026-07-04"]

    assert window["DISTINCT_QUALIFIED_THREADS"] == 1
    assert window["VERDICT"] == "MET"


# -- Was der Zähler NICHT weiss, behauptet er nicht --------------------------


def test_zahlungsabsicht_ohne_spalte_ist_nicht_gemessen_nicht_null() -> None:
    window = count_rows(parse_rows(_line("2026-07-10T10:00:00Z")))["windows"]["2026-07-04"]
    assert window["PAYMENT_INTENTS"] == "NOT_MEASURED"


def test_zahlungsabsicht_mit_achter_spalte_wird_gezaehlt() -> None:
    text = _line("2026-07-10T10:00:00Z") + " | ja"
    window = count_rows(parse_rows(text))["windows"]["2026-07-04"]
    assert window["PAYMENT_INTENTS"] == 1


def test_der_report_nennt_die_thread_naeherung_beim_namen() -> None:
    """``thread_id`` ist kein Personen-Identifikator — das muss dastehen."""
    report = count_rows(parse_rows(_line("2026-07-10T10:00:00Z")))
    assert "thread" in report["distinct_contacts_note"].lower()
    assert "DISTINCT_QUALIFIED_CONTACTS" not in report["windows"]["2026-07-04"]
