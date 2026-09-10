"""Ein Modellname gehoert an EINE Stelle — sonst veraltet die zweite still.

Der reale Fall (2026-09-09, kai-pi5): der Gemini-Schluessel wurde rotiert. Der
neue Schluessel erreicht ``gemini-2.5-flash`` nicht mehr — Google antwortet 404
mit dem Hinweis auf ``gemini-3.6-flash``. Auf dem Geraet war das reparierbar,
weil ``GEMINI_MODEL`` in der ``.env`` steht. Im Repo stand ``gemini-2.5-flash``
zu diesem Zeitpunkt an DREI unabhaengigen Stellen:

    app/core/settings.py            Field(default=...)          <- der Vertrag
    app/integrations/gemini/…       Konstruktor-Vorgabe          <- zweite Quelle
    app/orchestrator/trading_loop   ``or "gemini-2.5-flash"``    <- dritte Quelle

Jede Umgebung ohne gesetztes ``GEMINI_MODEL`` waere mit einem neuen Schluessel
in denselben 404 gelaufen — und zwar erst beim ersten echten Aufruf, nicht beim
Start. Genau deshalb faellt so etwas spaet auf.

Diese Datei haelt die Regel fest: ``ProviderSettings`` ist der Vertrag. Kein
zweiter Ort erfindet ein Modell, und wo keines konfiguriert ist, entsteht keines.
"""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
APP = REPO / "app"

#: Ein Default IST ein Modellname — er ENTHAELT keinen. Deshalb Vollmatch:
#: ``"[kai-chat] gpt-4o call failed: %s"`` ist Protokolltext, kein Vertrag, und
#: eine Teiltreffer-Regel schluege genau daran falschen Alarm. Das optionale
#: ``anbieter/``-Praefix deckt die LiteLLM-Schreibweise mit ab, ``claude-sonnet-4-6``
#: die Anthropic-Form, die kein Ziffernzeichen hinter dem Bindestrich hat.
_MODELL = re.compile(
    r"(?:[a-z_]+/)?(?:gemini|gpt|grok|whisper|claude)[-.][A-Za-z0-9.\-]*",
    re.IGNORECASE,
)

#: Wo ein Modellname stehen DARF — je Datei die ERLAUBTEN Namen, nicht die
#: Datei als ganze. Eine dateiweite Ausnahme haette in
#: ``kai_chat_engine.py`` mit der STT-Ausnahme auch den ``gpt-4o``-Fehler
#: durchgewinkt, den dieser Fix gerade entfernt.
#:
#: ``None`` heisst: hier sind Modellnamen die Sache selbst, nicht ein Default.
_ERLAUBT: dict[str, set[str] | None] = {
    # Der Vertrag.
    "app/core/settings.py": None,
    # Preistabelle: der Modellname ist der SCHLUESSEL. Eine Tabelle, die Namen
    # erraet, rechnet falsch (siehe Modul-Docstring dort).
    "app/ai/pricing.py": None,
    # --- bewusst offen, mit Grund, statt still ---------------------------------
    # STT hat kein Feld in ProviderSettings. Eines anzulegen ist eine
    # Design-Aenderung und braucht Platz in app/core/settings.py, das am
    # God-File-Ratchet auf null Headroom steht. Eigenes Vorhaben.
    "app/messaging/voice_transcriber.py": {"whisper-1"},
    "app/messaging/kai_chat_engine.py": {"whisper-1"},
    # `--consensus-model` ist ein Operator-Flag ohne Gegenstueck in
    # ProviderSettings; dasselbe gilt fuer die Vorgabe, die es durchreicht.
    "app/cli/commands/trading.py": {"gpt-4o-mini"},
    "app/cli/commands/tradingview.py": {"gpt-4o-mini"},
    "app/trading/signal_consensus.py": {"gpt-4o-mini"},
    "app/orchestrator/trading_loop.py": {"gpt-4o-mini"},
}


def _docstring_knoten(baum: ast.AST) -> set[int]:
    """Doku ist keine Konfiguration — Docstrings zaehlen nicht als Quelle."""
    ids: set[int] = set()
    for knoten in ast.walk(baum):
        if isinstance(knoten, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            koerper = getattr(knoten, "body", None)
            if koerper and isinstance(koerper[0], ast.Expr):
                wert = koerper[0].value
                if isinstance(wert, ast.Constant) and isinstance(wert.value, str):
                    ids.add(id(wert))
    return ids


def _modellnamen_im_code(pfad: Path) -> list[str]:
    baum = ast.parse(pfad.read_text(encoding="utf-8"))
    doku = _docstring_knoten(baum)
    treffer = []
    for knoten in ast.walk(baum):
        if isinstance(knoten, ast.Constant) and isinstance(knoten.value, str):
            if id(knoten) in doku:
                continue
            if _MODELL.fullmatch(knoten.value):
                treffer.append(knoten.value)
    return treffer


def test_der_pruefer_sieht_ueberhaupt_etwas() -> None:
    """Ein Waechter, der nichts findet, ist von einem gruenen nicht zu unterscheiden."""
    treffer = _modellnamen_im_code(APP / "core" / "settings.py")

    assert treffer, "im Vertrag selbst steht kein Modellname — der Pruefer greift nicht"
    assert any(t.startswith("gemini-") for t in treffer)


def test_kein_zweiter_ort_nennt_ein_modell() -> None:
    """Die zweite Quelle ist die, die veraltet."""
    zweite: dict[str, list[str]] = {}
    for pfad in sorted(APP.rglob("*.py")):
        rel = pfad.relative_to(REPO).as_posix()
        erlaubt = _ERLAUBT.get(rel, set())
        if erlaubt is None:
            continue
        treffer = {t for t in _modellnamen_im_code(pfad) if t not in erlaubt}
        if treffer:
            zweite[rel] = sorted(treffer)

    assert not zweite, (
        "Modellname ausserhalb von ProviderSettings — jede dieser Stellen kann "
        f"veralten, ohne dass es beim Start auffaellt: {zweite}"
    )


def test_die_provider_erfinden_kein_modell() -> None:
    """Ein Konstruktor-Default ist eine Quelle, die niemand pflegt.

    Wer einen Provider baut, weiss, welches Modell er meint — ``from_settings``
    reicht es aus dem Vertrag durch, Tests nennen es ausdruecklich.
    """
    from app.integrations.anthropic.provider import AnthropicAnalysisProvider
    from app.integrations.gemini.provider import GeminiAnalysisProvider
    from app.integrations.openai.provider import OpenAIAnalysisProvider
    from app.integrations.xai.provider import GrokAnalysisProvider

    for klasse in (
        GeminiAnalysisProvider,
        OpenAIAnalysisProvider,
        AnthropicAnalysisProvider,
        GrokAnalysisProvider,
    ):
        parameter = inspect.signature(klasse.__init__).parameters["model"]
        assert parameter.default is inspect.Parameter.empty, (
            f"{klasse.__name__} bringt eine eigene Modellvorgabe mit"
        )


def test_ohne_konfiguriertes_modell_entsteht_kein_gemini_validator() -> None:
    """Fail-closed: kein Modell heisst kein Aufruf, nicht ein geratenes Modell.

    Vorher stand hier ``or "gemini-2.5-flash"``. Mit einem rotierten Schluessel
    haette das einen Validator gebaut, der bei jedem Aufruf 404 bekommt.
    """
    from app.orchestrator.trading_loop import _build_consensus_validator

    class _Provider:
        openai_api_key = ""
        gemini_api_key = "irgendein-schluessel"
        gemini_model = ""

    class _Settings:
        providers = _Provider()

    validator = _build_consensus_validator(True, "gpt-4o-mini", _Settings())

    assert validator is None, "ohne Modell darf kein Gemini-Validator entstehen"


def test_mit_konfiguriertem_modell_entsteht_er_sehr_wohl() -> None:
    """Gegenprobe — sonst prueft der Test oben nur, dass nie etwas gebaut wird."""
    from app.orchestrator.trading_loop import _build_consensus_validator

    class _Provider:
        openai_api_key = ""
        gemini_api_key = "irgendein-schluessel"
        gemini_model = "gemini-3.6-flash"

    class _Settings:
        providers = _Provider()

    validator = _build_consensus_validator(True, "gpt-4o-mini", _Settings())

    assert validator is not None
