"""Kontrollierte Evidenz — und die Zusage, die sie erst prüfbar macht.

Der Befund, gegen den die halbe Datei steht, kam beim ersten Lauf des
Generators heraus und wäre sonst niemand aufgefallen: **ein ausgefallenes
Gateway riss den ganzen Aufruf mit.** `call_litellm_async` sagt zu, keine
Netzwerkfehler zu werfen — aber eine Zusage ist kein Zwang. Der Client-Aufbau
liegt außerhalb dieser Funktion, und eine geworfene Ausnahme ging ungebremst
durch `run_litellm` hindurch bis zum Aufrufer.

Im Schatten ist das die Umkehrung des Sinns: SHADOW heißt, der Transport läuft
mit und entscheidet nichts. Ein Fehler, der den Aufruf sprengt, entscheidet
alles — er nimmt dem Altpfad die Antwort weg, die dieser bereits hatte.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from scripts.litellm_shadow_eval.loader import load_evidence
from scripts.litellm_shadow_eval.pairing import pair_records
from scripts.litellm_shadow_replay.replay import (
    DIRECT_ANTWORT,
    FAELLE,
    Fall,
    erfolg,
    erzeuge_evidenz,
    http_fehler,
    pruefe_zusagen,
)


def _zeilen(pfad: Path) -> list[dict[str, Any]]:
    if not pfad.exists():
        return []
    return [json.loads(z) for z in pfad.read_text(encoding="utf-8").splitlines() if z.strip()]


def _shadow(pfad: Path) -> list[dict[str, Any]]:
    return [z for z in _zeilen(pfad) if z.get("transport") == "litellm"]


# ---------------------------------------------------------------------------
# Der Befund: ein ausgefallenes Gateway darf den Altpfad nicht mitreissen.
# ---------------------------------------------------------------------------


async def test_ein_ausgefallenes_gateway_nimmt_dem_altpfad_nicht_die_antwort(
    tmp_path: Path,
) -> None:
    pfad = tmp_path / "llm_telemetry.jsonl"
    fall = Fall("aus", (httpx.ConnectError("connection refused"),), erwartete_versuche=3)

    (beobachtung,) = await erzeuge_evidenz(pfad, faelle=(fall,))

    assert beobachtung["wert"] == DIRECT_ANTWORT, "der Altpfad hatte die Antwort bereits"
    assert beobachtung["transport"] == "direct"
    assert not pruefe_zusagen([beobachtung], faelle=(fall,))


async def test_ein_transportfehler_wird_zur_spur_statt_zum_abbruch(tmp_path: Path) -> None:
    """Ein Fehler ohne Zeile ist ein Fehler, den niemand zaehlt."""
    pfad = tmp_path / "llm_telemetry.jsonl"
    fall = Fall("aus", (httpx.ConnectError("nope"),), erwartete_versuche=3)

    await erzeuge_evidenz(pfad, faelle=(fall,))

    schatten = _shadow(pfad)
    assert len(schatten) == 3, "jeder physische Versuch hinterlaesst eine Zeile"
    assert {z["error_class"] for z in schatten} == {"transport"}
    assert all(z["ok"] is False for z in schatten)


async def test_auch_ein_kaputter_client_aufbau_bleibt_im_schatten(tmp_path: Path) -> None:
    """Der Aufbau liegt VOR dem Gateway — und war die zweite offene Flanke.

    Ohne die Absicherung käme ``execute_async`` gar nicht erst zum Zug, und der
    Altpfad wäre nicht einmal versucht worden: der Aufruf schlüge fehl, weil ein
    HTTP-Client nicht entstehen konnte, den er für die Antwort nicht braucht.
    """
    from app.ai.audit import llm_call_scope
    from app.ai.config import InferenceSettings
    from app.ai.runtime import LiteLLMRequest, invoke

    pfad = tmp_path / "llm_telemetry.jsonl"

    def kaputte_factory(**_: Any) -> httpx.AsyncClient:
        raise RuntimeError("client konnte nicht gebaut werden")

    async def direct_call() -> str:
        async with llm_call_scope(purpose="analysis", provider="openai", model="gpt-4o", path=pfad):
            return DIRECT_ANTWORT

    ergebnis = await invoke(
        purpose="analysis",
        direct_call=direct_call,
        direct_provider="openai",
        direct_model="gpt-4o",
        litellm=LiteLLMRequest(parser=lambda b: str(b)),
        settings=InferenceSettings(
            enabled=True,
            mode_ceiling="shadow",
            route_modes={"standard": "shadow"},
            max_attempts=1,
        ),
        telemetry_path=pfad,
        client_factory=kaputte_factory,
    )

    assert ergebnis.value == DIRECT_ANTWORT
    assert ergebnis.transport == "direct"
    (schatten,) = _shadow(pfad)
    assert schatten["error_class"] is not None
    assert schatten["ok"] is False


# ---------------------------------------------------------------------------
# Die Retry-Politik, an echten HTTP-Antworten gemessen.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "meldung", "erwartete_versuche", "klasse"),
    [
        (401, "invalid api key", 1, "auth"),
        (403, "forbidden", 1, "auth"),
        (429, "rate limit exceeded", 3, "rate_limit"),
        (503, "service unavailable", 3, "server"),
        (500, "internal error", 3, "server"),
    ],
)
async def test_der_statuscode_entscheidet_ueber_die_wiederholung(
    tmp_path: Path, status: int, meldung: str, erwartete_versuche: int, klasse: str
) -> None:
    pfad = tmp_path / "llm_telemetry.jsonl"
    fall = Fall(
        f"http{status}",
        (http_fehler(status, meldung=meldung),),
        erwartete_versuche=erwartete_versuche,
    )

    (beobachtung,) = await erzeuge_evidenz(pfad, faelle=(fall,))

    assert beobachtung["physische_versuche"] == erwartete_versuche
    assert {z["error_class"] for z in _shadow(pfad)} == {klasse}
    assert beobachtung["wert"] == DIRECT_ANTWORT


async def test_derselbe_statuscode_wird_verschieden_behandelt(tmp_path: Path) -> None:
    """429 ist wiederholbar — ein erschöpftes Kontingent nicht.

    Beides kommt als 429 herein. Ob wiederholt wird, entscheidet der Body, und
    genau diese Übersetzung würde ein Test verpassen, der die Transportfunktion
    ersetzt statt der HTTP-Antwort.
    """
    limit = tmp_path / "limit.jsonl"
    quota = tmp_path / "quota.jsonl"

    (mit_limit,) = await erzeuge_evidenz(
        limit, faelle=(Fall("limit", (http_fehler(429, meldung="rate limit exceeded"),)),)
    )
    (mit_quota,) = await erzeuge_evidenz(
        quota, faelle=(Fall("quota", (http_fehler(429, meldung="insufficient_quota"),)),)
    )

    assert mit_limit["physische_versuche"] == 3, "ein Limit kann sich entspannen"
    assert mit_quota["physische_versuche"] == 1, "ein Kontingent fuellt sich nicht durch Fragen"
    assert {z["error_class"] for z in _shadow(limit)} == {"rate_limit"}
    assert {z["error_class"] for z in _shadow(quota)} == {"quota"}


async def test_ein_timeout_wird_genau_einmal_wiederholt_wenn_es_dann_klappt(
    tmp_path: Path,
) -> None:
    pfad = tmp_path / "llm_telemetry.jsonl"
    fall = Fall("t", (httpx.ReadTimeout("read timeout"), erfolg()), erwartete_versuche=2)

    (beobachtung,) = await erzeuge_evidenz(pfad, faelle=(fall,))

    assert beobachtung["physische_versuche"] == 2
    schatten = _shadow(pfad)
    assert [z["ok"] for z in schatten] == [False, True]
    assert [z["attempt"] for z in schatten] == [1, 2]
    assert [z["retry_count"] for z in schatten] == [0, 1]


# ---------------------------------------------------------------------------
# Kosten und Identität kommen vom Gateway, nicht aus der Erwartung.
# ---------------------------------------------------------------------------


async def test_identitaet_und_kosten_stammen_aus_der_antwort(tmp_path: Path) -> None:
    pfad = tmp_path / "llm_telemetry.jsonl"

    await erzeuge_evidenz(pfad, faelle=(Fall("ok", (erfolg(kosten=0.0042),)),))

    (schatten,) = _shadow(pfad)
    assert schatten["actual_provider"] == "openai", "aus dem Header"
    assert schatten["actual_model"] == "gpt-4o-mini", "aus dem Body"
    assert schatten["identity_proven"] is True
    assert schatten["cost_usd"] == pytest.approx(0.0042)
    assert schatten["input_tokens"] == 118 and schatten["output_tokens"] == 37


async def test_ein_gateway_ohne_transportpreis_schaetzt_aus_der_preistabelle(
    tmp_path: Path,
) -> None:
    pfad = tmp_path / "llm_telemetry.jsonl"

    await erzeuge_evidenz(pfad, faelle=(Fall("ohne", (erfolg(kosten=None),)),))

    (schatten,) = _shadow(pfad)
    # Seit D-CORE-007 schaetzt die Telemetrie aus Tokens x Preistabelle, wenn der
    # Transport keinen Preis meldet — gekennzeichnet, nie eine erfundene Null.
    assert schatten["cost_usd"] is not None and schatten["cost_usd"] > 0
    assert schatten["cost_known"] is True
    assert str(schatten["cost_source"]).startswith("list_price")


# ---------------------------------------------------------------------------
# Die erzeugte Evidenz ist für den Harness lesbar.
# ---------------------------------------------------------------------------


async def test_jeder_lauf_ergibt_genau_ein_vollstaendiges_paar(tmp_path: Path) -> None:
    pfad = tmp_path / "llm_telemetry.jsonl"
    faelle = (
        Fall("ok", (erfolg(),), wiederholungen=4, erwartete_versuche=1),
        Fall("fehler", (http_fehler(401),), wiederholungen=2, erwartete_versuche=1),
    )

    beobachtungen = await erzeuge_evidenz(pfad, faelle=faelle)

    geladen = load_evidence([pfad])
    assert not geladen.issues, [i.code for i in geladen.issues]
    paare = pair_records(geladen.records)
    assert not paare.issues, [i.code for i in paare.issues]
    assert len(paare.pairs) == len(beobachtungen) == 6
    assert all(p.status.value == "VALID_PAIR" for p in paare.pairs)


async def test_ein_retry_erzeugt_mehr_zeilen_aber_nicht_mehr_paare(tmp_path: Path) -> None:
    """Sonst belohnte die Stichprobe genau das, was sie messen soll."""
    pfad = tmp_path / "llm_telemetry.jsonl"
    fall = Fall("5xx", (http_fehler(503),), wiederholungen=2, erwartete_versuche=3)

    await erzeuge_evidenz(pfad, faelle=(fall,))

    assert len(_zeilen(pfad)) == 8, "2x (3 Schatten + 1 direkt)"
    paare = pair_records(load_evidence([pfad]).records)
    assert len(paare.pairs) == 2, "zwei logische Aufrufe"


# ---------------------------------------------------------------------------
# Der Erzeuger benotet sich nicht selbst.
# ---------------------------------------------------------------------------


def test_die_zusagenpruefung_meldet_eine_ersetzte_antwort() -> None:
    fall = Fall("x", (erfolg(),), erwartete_versuche=1)
    verstoesse = pruefe_zusagen(
        [{"fall": "x", "wert": "schatten", "transport": "litellm", "physische_versuche": 1}],
        faelle=(fall,),
    )
    assert any("SHADOW hat die Antwort ersetzt" in v for v in verstoesse)
    assert any("statt direct" in v for v in verstoesse)


def test_die_zusagenpruefung_meldet_eine_abweichende_versuchszahl() -> None:
    fall = Fall("x", (erfolg(),), erwartete_versuche=1)
    verstoesse = pruefe_zusagen(
        [{"fall": "x", "wert": DIRECT_ANTWORT, "transport": "direct", "physische_versuche": 3}],
        faelle=(fall,),
    )
    assert any("3 Versuche, erwartet 1" in v for v in verstoesse)


def test_der_katalog_deckt_die_geforderten_faelle_ab() -> None:
    """Ein Shadow-Betrieb, der nur den Normalfall belegt, hat nichts belegt."""
    namen = {fall.name for fall in FAELLE}
    for pflicht in (
        "gateway_down",
        "gateway_timeout",
        "rate_limit_429",
        "server_5xx_erschoepft",
        "auth_401_ohne_retry",
        "forbidden_403_ohne_retry",
        "quota_ohne_retry",
        "kosten_unbekannt",
    ):
        assert pflicht in namen, pflicht
    assert all(fall.beschreibung for fall in FAELLE), "jeder Fall sagt, wofuer er steht"


def test_der_erzeuger_geht_nicht_ins_netz() -> None:
    """Er stellt Antworten nach — er holt keine."""
    import ast

    quelle = Path("scripts/litellm_shadow_replay/replay.py").read_text(encoding="utf-8")
    baum = ast.parse(quelle)
    aufrufe = {ast.unparse(k.func) for k in ast.walk(baum) if isinstance(k, ast.Call)}
    assert "httpx.AsyncClient" in aufrufe, "der Client entsteht -- aber mit MockTransport"
    assert "httpx.MockTransport" in aufrufe
    for verboten in ("httpx.get", "httpx.post", "requests.get", "socket.socket"):
        assert verboten not in aufrufe, verboten
