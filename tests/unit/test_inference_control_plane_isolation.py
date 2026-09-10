"""Die Inferenz-Ebene darf KAI nicht entwickeln, nur beraten.

Betriebsanweisung des Operators (2026-09-10): Runtime-Inferenz und
Softwareentwicklung bleiben getrennt. ``app/ai`` und LiteLLM erhalten keine
implizite Kontrolle ueber Repository, Git, CI, Deployment oder Zugangsdaten,
und die Architektur bleibt anbieterunabhaengig — KAI wird weiter primaer mit
Codex entwickelt und bei Bedarf durch Claude geprueft.

Das ist keine Absichtserklaerung, sondern hier gemessen. Ein Import, der die
Grenze verschiebt, faellt in diesem Test auf und nicht erst im Betrieb.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_APP_AI = Path(__file__).resolve().parents[2] / "app" / "ai"
_LITELLM_CONFIG = Path(__file__).resolve().parents[2] / "config" / "litellm.yaml"

#: Alles, was einen Prozess starten oder den Rechner fernsteuern koennte.
_VERBOTENE_MODULE = frozenset(
    {
        "subprocess",
        "multiprocessing",
        "pty",
        "os.system",
        "git",
        "pygit2",
        "dulwich",
        "github",
        "docker",
        "paramiko",
        "fabric",
    }
)

#: Schreibprimitive. Die Inferenz-Ebene liest Konfiguration und ruft Modelle —
#: sie legt keine Datei an. Telemetrie schreibt `app/observability`, EIN Strom
#: an EINER Stelle, und der geht nach `artifacts/`, nicht in den Quellbaum.
_VERBOTENE_SCHREIBWEGE = (
    ".write_text(",
    ".write_bytes(",
    ".mkdir(",
    ".unlink(",
    ".touch(",
    "shutil.rmtree",
    "os.remove",
    "os.rename",
)


def _module_dateien() -> list[Path]:
    return sorted(p for p in _APP_AI.rglob("*.py") if "__pycache__" not in p.parts)


def _importierte_namen(pfad: Path) -> set[str]:
    baum = ast.parse(pfad.read_text(encoding="utf-8"), filename=str(pfad))
    namen: set[str] = set()
    for knoten in ast.walk(baum):
        if isinstance(knoten, ast.Import):
            namen.update(alias.name.split(".")[0] for alias in knoten.names)
        elif isinstance(knoten, ast.ImportFrom) and knoten.module:
            namen.add(knoten.module.split(".")[0])
    return namen


def test_die_inferenz_ebene_kann_keinen_prozess_starten() -> None:
    """Kein Git, kein Shell-Aufruf, kein Deploy — auch nicht mittelbar."""
    treffer = {
        pfad.name: sorted(_importierte_namen(pfad) & _VERBOTENE_MODULE)
        for pfad in _module_dateien()
        if _importierte_namen(pfad) & _VERBOTENE_MODULE
    }
    assert treffer == {}


def test_die_inferenz_ebene_schreibt_keine_datei() -> None:
    """Keine autonome Selbstmodifikation: app/ai fasst den Quellbaum nicht an."""
    treffer = {
        pfad.name: [w for w in _VERBOTENE_SCHREIBWEGE if w in pfad.read_text(encoding="utf-8")]
        for pfad in _module_dateien()
        if any(w in pfad.read_text(encoding="utf-8") for w in _VERBOTENE_SCHREIBWEGE)
    }
    assert treffer == {}


def test_die_inferenz_ebene_liest_die_umgebung_nicht_selbst() -> None:
    """Zugangsdaten kommen aus den Settings, nicht aus ``os.environ``.

    Sonst koennte ein Modellpfad an eine Variable kommen, die niemand fuer ihn
    vorgesehen hat — ein GitHub-Token zum Beispiel.
    """
    treffer = [
        pfad.name
        for pfad in _module_dateien()
        if "os.environ" in pfad.read_text(encoding="utf-8")
        or "os.getenv" in pfad.read_text(encoding="utf-8")
    ]
    assert treffer == []


def test_kein_anbieter_ist_im_research_pfad_verdrahtet() -> None:
    """Der Research-Pfad nennt kein Modell und keinen Anbieter."""
    anbieter = ("moonshot", "kimi", "openai", "anthropic", "gemini", "deepseek")
    for name in ("research.py", "brief_synthesis.py"):
        text = (_APP_AI / name).read_text(encoding="utf-8").lower()
        gefunden = [a for a in anbieter if a in text]
        assert gefunden == [], f"{name} bindet {gefunden}"


def test_jede_litellm_route_holt_ihr_modell_aus_der_umgebung() -> None:
    """Kein Anbieter steht im Repository — auch nicht fuer die Research-Route."""
    text = _LITELLM_CONFIG.read_text(encoding="utf-8")
    modelle = [
        zeile.split("model:", 1)[1].strip()
        for zeile in text.splitlines()
        if zeile.strip().startswith("model:")
    ]
    assert modelle, "config/litellm.yaml nennt keine Route mehr"
    for modell in modelle:
        assert modell.startswith("os.environ/"), modell


@pytest.mark.parametrize("alias", ["kai-kimi-research"])
def test_der_routenname_ist_ein_alias_und_kein_modellvertrag(alias: str) -> None:
    """Der Alias darf einen Anbieter nennen — er waehlt keinen aus.

    ``kai-kimi-research`` ist der vom Operator gesetzte Routenname. Welches
    Modell dahinter antwortet, entscheidet ``KAI_LITELLM_RESEARCH_MODEL``.
    """
    from app.ai.config import InferenceSettings

    voreinstellung = InferenceSettings()
    assert voreinstellung.route_aliases["research"] == alias
    geaendert = InferenceSettings(route_aliases={"research": "kai-anderer-anbieter"})
    assert geaendert.route_aliases["research"] == "kai-anderer-anbieter"
