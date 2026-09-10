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

from pathlib import Path
from typing import Any

import httpx
import pytest

from app.ai.config import InferenceSettings
from app.ai.runtime import LiteLLMRequest
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


async def test_das_gate_faengt_die_abschneidung_vor_dem_parser() -> None:
    """Die Meldung mit `max_tokens` muss im BETRIEB erreichbar sein, nicht nur im Test.

    #946 laesst eine abgeschnittene Antwort bewusst durch den Transport
    (`ok=True`, `truncated=True`), damit die Runtime urteilen kann. #949 hat die
    Zusicherung entfernt, die den Parser-Aufruf vorschrieb. Erst dadurch ist
    dieses Gate ueberhaupt baubar -- vorher haette es eine fremde, gemergte
    Kontrolle rot gemacht.

    Geprueft wird am VERHALTEN: der Parser darf nicht mehr laufen, und die
    Klassifizierung muss `truncated` sein und nicht `schema`.
    """
    import httpx

    from app.ai.runtime import invoke

    gesehen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "gemini/gemini-3.6-flash",
                "choices": [
                    {"message": {"content": "Der groesste Risik"}, "finish_reason": "length"}
                ],
            },
            request=request,
        )

    def parser(body: dict[str, Any]) -> str:
        gesehen["parser"] = True
        return "sollte nie passieren"

    ergebnis = await invoke(
        purpose="analysis",
        direct_call=_direkt,
        direct_provider="openai",
        direct_model="gpt-4o",
        litellm=LiteLLMRequest(parser=parser, payload={"messages": [], "max_tokens": 1024}),
        settings=InferenceSettings(
            enabled=True, mode_ceiling="shadow", route_modes={"standard": "shadow"}
        ),
        client_factory=_client_factory(handler),
        sleeper=_kein_schlaf,
    )

    assert "parser" not in gesehen, "der Parser lief trotz Abschneidung"
    assert ergebnis.outcome is not None
    trace = ergebnis.outcome.litellm_attempts[0].trace
    assert trace.truncated is True
    # KEINE eigene Fehlerklasse: `ok` gehoert dem Transport, und der war
    # erfolgreich. Entscheidend ist, dass der Versuch scheitert und NICHT als
    # Schemafehler gilt -- sonst schickte die Klassifizierung den naechsten
    # Leser in den Parser statt zum Token-Deckel.
    assert trace.error_class != "schema"
    assert trace.ok, "ok gehoert dem Transport — das Urteil steht im error des Versuchs"
    from app.ai.audit import is_retryable_error_class

    assert not is_retryable_error_class(trace.error_class), (
        "eine Wiederholung traefe denselben Deckel"
    )


async def test_ohne_abschneidung_laeuft_der_parser_ganz_normal() -> None:
    """Gegenprobe: sonst zeigte der Test oben nur, dass nie geparst wird."""
    import httpx

    from app.ai.runtime import invoke

    gesehen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "gemini/gemini-3.6-flash",
                "choices": [{"message": {"content": "vollstaendig"}, "finish_reason": "stop"}],
            },
            request=request,
        )

    def parser(body: dict[str, Any]) -> str:
        gesehen["parser"] = True
        return str(body["choices"][0]["message"]["content"])

    await invoke(
        purpose="analysis",
        direct_call=_direkt,
        direct_provider="openai",
        direct_model="gpt-4o",
        litellm=LiteLLMRequest(parser=parser, payload={"messages": [], "max_tokens": 1024}),
        settings=InferenceSettings(
            enabled=True, mode_ceiling="shadow", route_modes={"standard": "shadow"}
        ),
        client_factory=_client_factory(handler),
        sleeper=_kein_schlaf,
    )

    assert gesehen.get("parser"), "der Parser wurde uebersprungen, obwohl nichts fehlte"


async def _direkt() -> str:
    return "direkt"


async def _kein_schlaf(_sekunden: float) -> None:
    return None


def _client_factory(handler: object) -> object:
    def bauen(**_: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]

    return bauen


async def test_ein_abgeschnittener_lauf_wird_nicht_als_erfolg_gezaehlt(tmp_path: Path) -> None:
    """Sonst zaehlte die erste echte SHADOW-Auswertung Abschneidungen als Erfolge.

    `trace.ok` gehoert dem TRANSPORT, und der war erfolgreich. `outcome` und
    `schema_status` beschreiben aber den VERSUCH, und aus dem entstand kein
    Wert. Auf `trace.ok` gestuetzt meldete die Zeile `outcome="success"` und
    `schema_status="valid"` fuer einen Aufruf ohne Analyse --
    `scripts/litellm_shadow_eval/metrics.py` bildet seine
    `outcome_distribution` genau daraus.

    Genau die Klasse, gegen die diese ganze Kette gebaut ist: gruen, obwohl
    unbrauchbar. Gefunden hat es die Parallelsitzung beim Nachpruefen des
    Gates, nicht der Test hier -- deshalb steht er jetzt hier.
    """
    import json

    import httpx

    from app.ai.runtime import invoke

    zeile = tmp_path / "telemetrie.jsonl"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "gemini/gemini-3.6-flash",
                "choices": [
                    {"message": {"content": "Der groesste Risik"}, "finish_reason": "length"}
                ],
            },
            request=request,
        )

    await invoke(
        purpose="analysis",
        direct_call=_direkt,
        direct_provider="openai",
        direct_model="gpt-4o",
        litellm=LiteLLMRequest(
            parser=lambda _b: "nie", payload={"messages": [], "max_tokens": 1024}
        ),
        settings=InferenceSettings(
            enabled=True, mode_ceiling="shadow", route_modes={"standard": "shadow"}
        ),
        client_factory=_client_factory(handler),
        sleeper=_kein_schlaf,
        telemetry_path=zeile,
    )

    eintraege = [json.loads(z) for z in zeile.read_text(encoding="utf-8").splitlines() if z.strip()]
    versuche = [e for e in eintraege if e.get("transport") == "litellm"]
    assert versuche, "kein LiteLLM-Versuch in der Zeile"
    for e in versuche:
        assert e.get("truncated") is True
        assert e.get("outcome") != "success", "abgeschnitten als Erfolg gezaehlt"
        assert e.get("schema_status") != "valid", "abgeschnitten als schemagueltig gezaehlt"


async def test_unter_primary_faellt_eine_abschneidung_auf_den_direktpfad_zurueck() -> None:
    """Der kontrollierte Rueckfall aus ADR 0017 — genau fuer diesen Fall gedacht.

    `InferenceResult.ok` liest den letzten Trace, und der bleibt bei einer
    Abschneidung absichtlich `ok` (der Transport war erfolgreich). Ohne Zusatz
    galte der Versuch damit als GETRAGEN: `litellm_carried` waere True, der
    Direktpfad wuerde uebersprungen, und die Analyse stuerbe an einem zu
    kleinen Token-Budget, statt auf den Direktanbieter zurueckzufallen.

    Heute beisst das nicht, weil PRIMARY aus ist. Es waere eine Mine fuer die
    spaetere Umstellung — gefunden von der Parallelsitzung beim Nachpruefen der
    Folgen des Gates, dritte Schicht derselben Kette.
    """
    import httpx

    from app.ai.runtime import invoke

    direkt_gerufen: dict[str, bool] = {}

    async def direkt() -> str:
        direkt_gerufen["ja"] = True
        return "direkt"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "gemini/gemini-3.6-flash",
                "choices": [
                    {"message": {"content": "Der groesste Risik"}, "finish_reason": "length"}
                ],
            },
            request=request,
        )

    ergebnis = await invoke(
        purpose="analysis",
        direct_call=direkt,
        direct_provider="openai",
        direct_model="gpt-4o",
        litellm=LiteLLMRequest(
            parser=lambda _b: "nie", payload={"messages": [], "max_tokens": 1024}
        ),
        settings=InferenceSettings(
            enabled=True, mode_ceiling="primary", route_modes={"standard": "primary"}
        ),
        client_factory=_client_factory(handler),
        sleeper=_kein_schlaf,
    )

    assert direkt_gerufen.get("ja"), "der Direktpfad wurde uebersprungen — der Rueckfall fehlt"
    assert ergebnis.value == "direkt"
    assert ergebnis.transport == "direct"
    assert ergebnis.outcome is not None
    assert ergebnis.outcome.gateway.fell_back, "der Rueckfall wurde nicht als solcher gemeldet"
