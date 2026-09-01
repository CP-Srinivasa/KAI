"""Der Adapter und die installierte `anthropic`-Bibliothek müssen zusammenpassen.

Schwesterdatei zu ``test_youtube_transcript_api_contract.py``, aus demselben
Grund und nach derselben Lehre: **ein Test, der die Bibliothek mockt, bestätigt
die gemockte API, nicht die installierte.** Genau so sind die YouTube-Transkripte
nach einem Bibliotheks-Upgrade monatelang still gestorben — der Aufruf lief in
ein `except`, gab `None` zurück und protokollierte nichts
([[feedback_mock_tests_cannot_see_moved_apis]]).

Anlass 2026-08-31: der wöchentliche Lock-Refresh (#808) hebt `anthropic` von
`0.122.0` auf **`1.2.0`** — einen Major. Die 1.x-Reihe bringt reale
Bruchstellen mit (Wechsel auf `httpx2`, awaited async `.with_raw_response`,
entfernte Alt-Parameter, Python >= 3.10). Ob KAI davon betroffen ist, ließ sich
nur durch Handarbeit beantworten, weil nichts die Bindung prüfte. Live gemessen
liefen zu diesem Zeitpunkt **drei** Versionen nebeneinander: 0.85.0 lokal,
0.120.2 auf der Pi, 1.2.0 im vorgeschlagenen Lock.

Geprüft wird deshalb **nur die Fläche, die KAI wirklich benutzt** — nicht die
SDK-Oberfläche insgesamt. Wächst die Nutzung, wächst dieser Test mit; was hier
nicht steht, benutzt KAI nicht:

* ``app/integrations/anthropic/provider.py`` — ``AsyncAnthropic(api_key, timeout)``
  und ``messages.create(model, system, max_tokens, messages, tools, tool_choice)``
* ``app/intelligence/providers.py`` — ``Anthropic(api_key, timeout)`` und
  ``messages.create(model, system, max_tokens, messages)``

Kein Netzaufruf: geprüft werden Existenz und Signaturen, nie eine Antwort. Der
Schlüssel ist absichtlich ungültig — er verlässt den Prozess nicht.
"""

from __future__ import annotations

import inspect

import anthropic
import pytest

#: Absichtlich ungültig. Wird nie gesendet — der Client wird nur konstruiert.
_KEIN_ECHTER_SCHLUESSEL = "sk-ant-vertragstest-nicht-echt"

#: Genau die Parameter, die die beiden Aufrufstellen setzen.
_BENUTZTE_CREATE_PARAMETER = (
    "model",
    "system",
    "max_tokens",
    "messages",
    "tools",
    "tool_choice",
)


@pytest.mark.parametrize("cls_name", ["Anthropic", "AsyncAnthropic"])
def test_die_installierte_bibliothek_bietet_die_client_klasse(cls_name: str) -> None:
    """Bricht laut, wenn ein Upgrade eine der beiden Klassen verschiebt."""
    assert hasattr(anthropic, cls_name), (
        f"anthropic hat kein {cls_name} mehr — die API hat sich bewegt. "
        "Den Adapter anpassen, nicht diesen Test."
    )


@pytest.mark.parametrize("cls_name", ["Anthropic", "AsyncAnthropic"])
def test_der_konstruktor_nimmt_einen_timeout_als_zahl(cls_name: str) -> None:
    """KAI übergibt `timeout=<int|float>`, KEIN ``anthropic.Timeout``-Objekt.

    Das ist der Unterschied, der den httpx2-Wechsel der 1.x-Reihe für KAI
    harmlos macht: ein Timeout-OBJEKT stammte aus der HTTP-Bibliothek und wäre
    bei deren Austausch gebrochen. Eine nackte Zahl nicht.
    """
    cls = getattr(anthropic, cls_name)
    params = inspect.signature(cls.__init__).parameters
    assert "api_key" in params
    assert "timeout" in params

    client = cls(api_key=_KEIN_ECHTER_SCHLUESSEL, timeout=30)
    assert client is not None


@pytest.mark.parametrize("cls_name", ["Anthropic", "AsyncAnthropic"])
@pytest.mark.parametrize("parameter", _BENUTZTE_CREATE_PARAMETER)
def test_messages_create_nimmt_die_von_kai_gesetzten_parameter(
    cls_name: str, parameter: str
) -> None:
    """Jeder einzeln — eine Sammel-Assertion verstecke, WELCHER Parameter fehlt."""
    client = getattr(anthropic, cls_name)(api_key=_KEIN_ECHTER_SCHLUESSEL, timeout=30)
    create = client.messages.create
    assert parameter in inspect.signature(create).parameters, (
        f"{cls_name}.messages.create nimmt {parameter!r} nicht mehr — "
        "Aufrufstelle prüfen (app/integrations/anthropic/provider.py, "
        "app/intelligence/providers.py)."
    )


@pytest.mark.parametrize(
    "exc_name",
    ["RateLimitError", "APIStatusError", "APIConnectionError", "BadRequestError"],
)
def test_die_gefangenen_ausnahmen_existieren(exc_name: str) -> None:
    """Eine verschwundene Ausnahmeklasse macht aus einem Fehlerpfad einen Absturz."""
    assert hasattr(anthropic, exc_name)


def test_die_gelesenen_antwortfelder_existieren() -> None:
    """KAI liest ``usage.input_tokens``/``output_tokens`` und ``block.type/name/input``.

    Diese Felder werden mit ``getattr(..., default)`` gelesen — ein Wegfall
    würde also NICHT krachen, sondern still Nullen liefern. Genau deshalb steht
    er hier: die stille Variante ist die gefährlichere.
    """
    from anthropic.types import Message, ToolUseBlock, Usage

    for feld in ("input_tokens", "output_tokens"):
        assert feld in Usage.model_fields, f"Usage.{feld} fehlt"
    for feld in ("type", "name", "input"):
        assert feld in ToolUseBlock.model_fields, f"ToolUseBlock.{feld} fehlt"
    assert "content" in Message.model_fields
    assert "usage" in Message.model_fields


def test_der_adapter_ruft_keine_entfernte_alt_api() -> None:
    """Konkreter Regressionsschutz gegen die 1.x-Bruchstellen.

    Text Completions sind in 1.x entfernt, und `.with_raw_response` ist im
    async-Pfad awaited. KAI benutzt beides nicht — dieser Test hält das fest,
    damit es nicht versehentlich eingeführt wird.
    """
    from app.integrations.anthropic import provider

    quelle = inspect.getsource(provider)
    assert "completions" not in quelle, "Text Completions sind in anthropic 1.x entfernt."
    assert "with_raw_response" not in quelle, (
        "with_raw_response ist im async-Pfad von 1.x awaited — Aufrufform prüfen."
    )
