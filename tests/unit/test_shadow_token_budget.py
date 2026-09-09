"""Ein abgeschnittenes JSON ist kein kaputtes Modell, sondern ein zu kleines Budget.

Am 2026-09-09 auf kai-pi5 mit der ECHTEN Schattennutzlast gemessen
(``SYSTEM_PROMPT_V1``, ``response_format=json_object``, Text auf
``_MAX_TEXT_CHARS`` gekuerzt), vier reale Dokumente aus dem laufenden Betrieb,
je zwei Laeufe, ``gemini/gemini-3.6-flash``:

    Fall  Variante   prompt  completion  davon Denken   ueber 1024?
    A     baseline     1210        1008           639   nein
    A     baseline     1210        1288           911   JA
    B     baseline     1824         948           578   nein
    B     baseline     1824        1010           647   nein
    C     baseline     1790        1506          1136   JA
    C     baseline     1790        1291           912   JA
    D     baseline     2466         814           405   nein
    D     baseline     2466        1086           679   JA

Vier von acht Laeufen brauchten MEHR als die 1024 Token, die hier bis dahin
fest verdrahtet waren. Der Denkanteil schwankt zwischen 405 und 1136 Token und
zaehlt gegen dasselbe Budget wie die Antwort -- bei denkenden Modellen ist ein
Ausgabedeckel deshalb kein Ausgabedeckel.

Und der Fehler log sich falsch: die abgeschnittene Antwort erreichte den
Parser als halbes JSON, und der meldete `Invalid JSON: EOF while parsing a
string`. Wer das las, suchte beim Modell. Genau diese Verwechslung schliesst
die zweite Kontrolle hier.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.analysis.ai_control_plane import MAX_TOKENS, parse_analysis_body

_GUELTIG = (
    '{"sentiment_label": "bullish", "sentiment_score": 0.5, "relevance_score": 0.8,'
    ' "impact_score": 0.6, "confidence_score": 0.9, "novelty_score": 0.5,'
    ' "spam_probability": 0.1}'
)


def _body(content: str, finish: str = "stop") -> dict[str, Any]:
    return {"choices": [{"message": {"content": content}, "finish_reason": finish}]}


def test_das_budget_deckt_den_gemessenen_bedarf() -> None:
    """1024 tat es nicht -- und der hoechste gemessene Bedarf war 1506."""
    assert MAX_TOKENS > 1506, "unter dem gemessenen Spitzenbedarf"


def test_eine_gueltige_antwort_wird_gelesen() -> None:
    """Ohne diese Kontrolle koennte alles andere gruen sein, weil nie etwas ankommt."""
    ergebnis = parse_analysis_body(_body(_GUELTIG), user_prompt="egal")

    assert ergebnis.sentiment_label.value == "bullish"
    assert ergebnis.raw_prompt == "egal"


def test_abgeschnitten_meldet_abgeschnitten_und_nicht_kaputtes_json() -> None:
    """Der Unterschied zwischen "zu kleines Budget" und "Modell antwortet Unsinn".

    Beides endet ohne Ergebnis, aber nur eines davon behebt der Operator an der
    richtigen Stelle.
    """
    halb = _GUELTIG[: len(_GUELTIG) // 2]

    with pytest.raises(ValueError) as fehler:
        parse_analysis_body(_body(halb, finish="length"), user_prompt="egal")

    text = str(fehler.value).lower()
    assert "truncat" in text or "abgeschnitten" in text, str(fehler.value)
    assert str(MAX_TOKENS) in str(fehler.value), "das Budget gehoert in die Meldung"


def test_ein_echter_json_fehler_bleibt_ein_json_fehler() -> None:
    """Gegenprobe: sonst verdeckte die neue Meldung jeden echten Formfehler.

    ``finish_reason=stop`` heisst, das Modell war fertig. Was dann nicht
    validiert, ist ein Inhaltsproblem und darf nicht als Budgetproblem
    erscheinen.
    """
    with pytest.raises(ValueError) as fehler:
        parse_analysis_body(_body('{"sentiment_label": "bullish"', finish="stop"), user_prompt="x")

    assert "truncat" not in str(fehler.value).lower()


def test_eine_vollstaendige_antwort_mit_length_ist_trotzdem_verdaechtig() -> None:
    """`length` UND gueltiges JSON: selten, aber dann fehlt hinten etwas.

    Der Deckel hat gegriffen. Dass sich das Ergebnis zufaellig parsen laesst,
    macht es nicht vollstaendig -- und stillschweigend durchzulassen hiesse,
    eine gekuerzte Analyse wie eine ganze zu behandeln.
    """
    with pytest.raises(ValueError) as fehler:
        parse_analysis_body(_body(_GUELTIG, finish="length"), user_prompt="x")

    assert "truncat" in str(fehler.value).lower()


def test_ohne_choices_bleibt_die_alte_meldung() -> None:
    with pytest.raises(ValueError, match="no choices"):
        parse_analysis_body({"choices": []}, user_prompt="x")


def test_leerer_inhalt_bleibt_die_alte_meldung() -> None:
    with pytest.raises(ValueError, match="no JSON content"):
        parse_analysis_body(_body(""), user_prompt="x")


def test_die_abschneidung_wird_nicht_zweitgeschrieben(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ein Satz, eine Definition — sonst wird eine davon irgendwann nachgezogen.

    `finish_reason == "length"` beantwortet der Transport (#946). Wuerde diese
    Datei die Frage noch einmal selbst stellen, gaebe es zwei Wahrheiten
    darueber, und sie muessten von Hand synchron gehalten werden.

    Geprueft wird das am VERHALTEN: wenn das Transport-Praedikat "nein" sagt,
    darf hier nichts mehr als abgeschnitten gelten -- auch dann nicht, wenn im
    Koerper `finish_reason=length` steht.
    """
    import app.analysis.ai_control_plane as modul

    monkeypatch.setattr(modul, "ist_abgeschnitten", lambda _body: False)

    ergebnis = parse_analysis_body(_body(_GUELTIG, finish="length"), user_prompt="x")

    assert ergebnis.sentiment_label.value == "bullish"


def test_und_umgekehrt_entscheidet_allein_das_praedikat(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gegenprobe: sagt der Transport "ja", scheitert es hier — ohne `length`."""
    import app.analysis.ai_control_plane as modul

    monkeypatch.setattr(modul, "ist_abgeschnitten", lambda _body: True)

    with pytest.raises(ValueError, match="truncated"):
        parse_analysis_body(_body(_GUELTIG, finish="stop"), user_prompt="x")
