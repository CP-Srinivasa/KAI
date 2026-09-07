"""Kontrollierte Shadow-Evidenz erzeugen — offline, ohne Netz, ohne Anbieter.

Der S6-Harness bewertet Evidenz. Diese Datei stellt sie her, und zwar fuer die
Faelle, auf die man nicht warten kann: ein Gateway, das ausfaellt; ein Upstream,
der 429 antwortet; ein Schluessel, der abgelehnt wird. Ein Shadow-Betrieb, der
nur den Normalfall belegt, hat nichts belegt — er hat zugesehen.

WAS HIER ECHT IST UND WAS NICHT

Nachgestellt wird ausschliesslich die HTTP-ANTWORT, ueber ``httpx.MockTransport``
an genau der Naht, die ``runtime.invoke`` dafuer schon vorsieht
(``client_factory``). Alles danach ist der Produktionsweg: ``call_litellm_async``
setzt die Anfrage zusammen, ``trace_from_response`` liest Modell, Anbieter,
Kosten und Fehlerklasse aus Body und Headern, die Control-Plane entscheidet
ueber Modus, Circuit, Budget und Wiederholung, und derselbe Schreiber legt die
Telemetriezeile ab wie im Betrieb.

Bewusst NICHT gepatcht wird ``call_litellm_async`` selbst. Wer die
Transportfunktion ersetzt, prueft seine eigene Nachbildung: die Uebersetzung
einer 429-Antwort in ``error_class="rate_limit"`` faende dann gar nicht statt,
und genau sie ist die Zusage, um die es geht.

Ein echter 5xx liesse sich nur herbeifuehren, indem man auf einen fremden
Ausfall wartet oder ihn provoziert. Gemessen wird hier ohnehin nicht der
Upstream, sondern wie KAI auf ihn reagiert — und das ist die Frage, die vor
einer Graduierung zaehlt.

WAS KONTROLLIERTE EVIDENZ BELEGT -- UND WAS NICHT

Sie belegt die REAKTION auf einen Fall, nicht die Reife einer Route. Wer
absichtlich Fehlerfaelle einstreut und die Erfolgsquote derselben Stichprobe
gegen eine Graduierungsschwelle haelt, misst die eigene Testanlage: je
gruendlicher man prueft, desto schlechter sieht die Route aus.

Deshalb gehoeren die beiden Dinge getrennt. Die Graduierungs-Stichprobe kommt
aus echtem Shadow-Betrieb; diese Faelle beweisen daneben, dass 401 nicht
wiederholt wird, dass ein Timeout genau einmal wiederholt wird und dass ein
ausgefallenes Gateway den Altpfad nicht mitreisst. Beides in einen Topf zu
werfen, waere kein strengerer Nachweis, sondern gar keiner.

WAS DIESE DATEI NIEMALS TUT

Sie oeffnet keine Verbindung, aktiviert keinen Modus, ruft keinen Anbieter und
schreibt in keinen Produktionspfad. Der Zielpfad der Telemetrie wird
uebergeben; eine bestehende Datei wird nicht stillschweigend verlaengert.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from app.ai.audit import llm_call_scope
from app.ai.config import InferenceSettings
from app.ai.runtime import LiteLLMRequest, invoke

#: Der Alias, den die Standard-Route anfordert. Nur ein Etikett -- welches
#: Modell tatsaechlich antwortete, sagt der Upstream, nicht KAI.
ALIAS = "kai-standard"

DIRECT_ANTWORT = "direkt"
SHADOW_ANTWORT = "schatten"

#: Header, wie ein LiteLLM-Gateway sie mitschickt. Die Namen stammen aus
#: ``app.integrations.litellm.provider`` -- wer sie hier frei erfindet, prueft
#: seine Erfindung.
_PROVIDER_HEADER = "x-litellm-model-provider"
_COST_HEADER = "x-litellm-response-cost"
_CALL_ID_HEADER = "x-litellm-call-id"


@dataclass(frozen=True, slots=True)
class Fall:
    """Ein benannter Betriebsfall und die Antworten, die das Gateway gaebe."""

    name: str
    #: Antwort auf Versuch *n* (0-basiert). Eine Ausnahme bedeutet: der Aufruf
    #: erreichte das Gateway nicht -- Verbindung verweigert, Zeitablauf.
    antworten: tuple[httpx.Response | BaseException, ...]
    wiederholungen: int = 1
    #: Erwartete Zahl physischer Versuche pro logischem Aufruf. ``None`` heisst:
    #: nicht vorab festgelegt (etwa wenn ein Circuit dazwischenfaehrt).
    erwartete_versuche: int | None = None
    beschreibung: str = ""


@dataclass
class _Gateway:
    """Beantwortet Anfragen der Reihe nach -- ein Gateway auf Abruf."""

    antworten: Sequence[httpx.Response | BaseException]
    aufrufe: int = field(default=0, init=False)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        index = min(self.aufrufe, len(self.antworten) - 1)
        self.aufrufe += 1
        antwort = self.antworten[index]
        if isinstance(antwort, BaseException):
            raise antwort
        # Jede Antwort einmal frisch aufbauen: eine `httpx.Response` ist nach
        # dem Lesen verbraucht, und ein zweiter Versuch bekaeme sonst eine
        # leere Huelle statt derselben Antwort.
        return httpx.Response(
            status_code=antwort.status_code,
            headers=dict(antwort.headers),
            content=antwort.content,
            request=request,
        )


def erfolg(*, kosten: float | None = 0.001) -> httpx.Response:
    """Eine Antwort, wie ein gesundes Gateway sie gibt."""
    headers = {_PROVIDER_HEADER: "openai", _CALL_ID_HEADER: "upstream-1"}
    if kosten is not None:
        headers[_COST_HEADER] = str(kosten)
    return httpx.Response(
        200,
        headers=headers,
        json={
            "model": "gpt-4o-mini",
            "choices": [{"message": {"content": SHADOW_ANTWORT}}],
            "usage": {"prompt_tokens": 118, "completion_tokens": 37},
        },
    )


def http_fehler(status: int, *, meldung: str = "upstream error") -> httpx.Response:
    """Eine Fehlantwort MIT Zeile -- das Gateway antwortet, es schweigt nicht."""
    return httpx.Response(status, json={"error": meldung})


#: Die Faelle, die vor einer Graduierung belegt sein muessen. Die Auswahl ist
#: nicht beliebig: jeder Eintrag steht fuer eine Zusage der Architektur.
FAELLE: tuple[Fall, ...] = (
    Fall(
        "gesund",
        (erfolg(),),
        wiederholungen=120,
        erwartete_versuche=1,
        beschreibung="Normalbetrieb -- die Stichprobe, gegen die alles andere gemessen wird",
    ),
    Fall(
        "gateway_down",
        (httpx.ConnectError("connection refused"),),
        wiederholungen=3,
        erwartete_versuche=3,
        beschreibung="Das Gateway laeuft nicht. Der Altpfad muss trotzdem antworten. "
        "Ein Verbindungsfehler kann voruebergehend sein, also wird bis zum Deckel "
        "wiederholt -- aber eben nur bis dorthin.",
    ),
    Fall(
        "gateway_timeout",
        (httpx.ReadTimeout("read timeout"), erfolg()),
        wiederholungen=3,
        erwartete_versuche=2,
        beschreibung="Ein Zeitablauf vertraegt eine Wiederholung -- genau eine reicht hier.",
    ),
    Fall(
        "rate_limit_429",
        (http_fehler(429, meldung="rate limit exceeded"), erfolg()),
        wiederholungen=3,
        erwartete_versuche=2,
        beschreibung="429 ist wiederholbar. Der zweite Versuch traegt das Ergebnis.",
    ),
    Fall(
        "server_5xx_erschoepft",
        (http_fehler(503, meldung="service unavailable"),),
        wiederholungen=3,
        erwartete_versuche=3,
        beschreibung="5xx wird wiederholt -- bis zum Deckel, nicht darueber hinaus.",
    ),
    Fall(
        "auth_401_ohne_retry",
        (http_fehler(401, meldung="invalid api key"),),
        wiederholungen=2,
        erwartete_versuche=1,
        beschreibung="Ein abgelehnter Schluessel wird durch Wiederholung nicht angenommen.",
    ),
    Fall(
        "forbidden_403_ohne_retry",
        (http_fehler(403, meldung="forbidden"),),
        wiederholungen=2,
        erwartete_versuche=1,
        beschreibung="Dasselbe fuer 403: dreimal dieselbe Ablehnung ist kein Erkenntnisgewinn.",
    ),
    Fall(
        "quota_ohne_retry",
        (http_fehler(429, meldung="insufficient_quota for this key"),),
        wiederholungen=2,
        erwartete_versuche=1,
        beschreibung="Ein erschoepftes Kontingent fuellt sich nicht durch Nachfragen -- "
        "obwohl derselbe Statuscode sonst wiederholbar waere.",
    ),
    Fall(
        "kosten_unbekannt",
        (erfolg(kosten=None),),
        wiederholungen=5,
        erwartete_versuche=1,
        beschreibung="Ein Gateway ohne Preisangabe. UNKNOWN darf nicht zu 0 werden.",
    ),
)


async def _einen_lauf(
    fall: Fall,
    *,
    telemetrie: Path,
    max_versuche: int,
) -> dict[str, Any]:
    """Einen logischen Aufruf durch die ECHTE Control-Plane fuehren."""
    gateway = _Gateway(fall.antworten)

    def client_factory(**kwargs: Any) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(gateway), **kwargs)

    async def direct_call() -> str:
        async with llm_call_scope(
            purpose="analysis", provider="openai", model="gpt-4o", path=telemetrie
        ) as scope:
            scope.set_tokens(118, 41)
            return DIRECT_ANTWORT

    def parse(koerper: dict[str, Any]) -> str:
        return str(koerper["choices"][0]["message"]["content"])

    ergebnis = await invoke(
        purpose="analysis",
        direct_call=direct_call,
        direct_provider="openai",
        direct_model="gpt-4o",
        litellm=LiteLLMRequest(parser=parse),
        settings=InferenceSettings(
            enabled=True,
            mode_ceiling="shadow",
            route_modes={"standard": "shadow"},
            max_attempts=max_versuche,
            backoff_base_seconds=0.0,
            backoff_max_seconds=0.0,
            jitter_max_seconds=0.0,
        ),
        telemetry_path=telemetrie,
        client_factory=client_factory,
    )

    return {
        "fall": fall.name,
        "wert": ergebnis.value,
        "transport": ergebnis.transport,
        "physische_versuche": gateway.aufrufe,
    }


async def erzeuge_evidenz(
    telemetrie: Path,
    *,
    faelle: Sequence[Fall] = FAELLE,
    max_versuche: int = 3,
    fortschritt: Callable[[str], None] | None = None,
) -> list[dict[str, Any]]:
    """Alle Faelle fahren und die Beobachtungen zurueckgeben.

    Der Rueckgabewert ist die BEOBACHTUNG, nicht die Bewertung. Bewertet wird
    ausschliesslich in ``engine.evaluate`` -- ein Erzeuger, der sein eigenes
    Ergebnis benotet, waere kein Beweis.
    """
    beobachtungen: list[dict[str, Any]] = []
    for fall in faelle:
        for _ in range(fall.wiederholungen):
            beobachtungen.append(
                await _einen_lauf(fall, telemetrie=telemetrie, max_versuche=max_versuche)
            )
        if fortschritt is not None:
            fortschritt(f"{fall.name}: {fall.wiederholungen}x")
    return beobachtungen


def pruefe_zusagen(
    beobachtungen: Sequence[dict[str, Any]], *, faelle: Sequence[Fall] = FAELLE
) -> list[str]:
    """Die Zusagen, die schon an der Beobachtung scheitern koennen.

    Bewusst schmal: hier steht nur, was ohne Auswertung entscheidbar ist. Alles
    Weitere gehoert in den Harness.
    """
    verstoesse: list[str] = []
    nach_fall: dict[str, list[dict[str, Any]]] = {}
    for beobachtung in beobachtungen:
        nach_fall.setdefault(str(beobachtung["fall"]), []).append(beobachtung)

    for fall in faelle:
        eintraege = nach_fall.get(fall.name, [])
        if len(eintraege) != fall.wiederholungen:
            verstoesse.append(f"{fall.name}: {len(eintraege)} statt {fall.wiederholungen} Laeufe")
        for eintrag in eintraege:
            # SHADOW ersetzt die Antwort nie -- unabhaengig davon, wie gut das
            # Gateway geantwortet hat.
            if eintrag["wert"] != DIRECT_ANTWORT:
                verstoesse.append(f"{fall.name}: SHADOW hat die Antwort ersetzt")
            if eintrag["transport"] != "direct":
                verstoesse.append(f"{fall.name}: transport={eintrag['transport']} statt direct")
            if (
                fall.erwartete_versuche is not None
                and eintrag["physische_versuche"] != fall.erwartete_versuche
            ):
                verstoesse.append(
                    f"{fall.name}: {eintrag['physische_versuche']} Versuche, "
                    f"erwartet {fall.erwartete_versuche}"
                )
    return verstoesse


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Kontrollierte Shadow-Evidenz erzeugen")
    parser.add_argument("--out", required=True, type=Path, help="Zielpfad der Telemetrie")
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument(
        "--append",
        action="store_true",
        help="an eine bestehende Datei anhaengen (mischt Laeufe -- bewusste Entscheidung)",
    )
    args = parser.parse_args(argv)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    # Eine bestehende Datei wird NICHT stillschweigend verlaengert. Zwei Laeufe
    # in einer Datei sehen aus wie ein grosser Lauf, und die Stichprobe waere
    # dann eine Summe aus Bedingungen, die nie gleichzeitig galten. Genau
    # dieser Fehler ist beim Bau dieser Datei passiert: ein abgebrochener
    # erster Lauf hinterliess 120 Paare, der zweite kam obendrauf, und die
    # Auswertung meldete 263 Paare fuer 143 Aufrufe -- rechnerisch stimmig,
    # inhaltlich falsch.
    if args.out.exists() and not args.append:
        print(
            f"ABBRUCH: {args.out.name} existiert bereits. Evidenz aus zwei Laeufen zu "
            f"mischen ergibt eine Stichprobe, die es nie gab. --append erzwingt es.",
            file=sys.stderr,
        )
        return 2

    beobachtungen = asyncio.run(
        erzeuge_evidenz(args.out, max_versuche=args.max_attempts, fortschritt=print)
    )
    verstoesse = pruefe_zusagen(beobachtungen)
    print(f"{len(beobachtungen)} logische Aufrufe -> {args.out}")
    for verstoss in verstoesse:
        print(f"VERSTOSS: {verstoss}", file=sys.stderr)
    return 1 if verstoesse else 0


__all__ = [
    "ALIAS",
    "DIRECT_ANTWORT",
    "FAELLE",
    "SHADOW_ANTWORT",
    "Fall",
    "erfolg",
    "erzeuge_evidenz",
    "http_fehler",
    "main",
    "pruefe_zusagen",
]


if __name__ == "__main__":
    raise SystemExit(main())
