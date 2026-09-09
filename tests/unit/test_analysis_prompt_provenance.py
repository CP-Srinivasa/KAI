"""Die Provenienz des Analyse-System-Prompts muss in der Zeile stehen — und stimmen.

Ein Versionswechsel am Prompt ist ohne dieses Feld nur nominell auditierbar:
V1- und V2-Läufe ließen sich allein über den Zeitstempel des Umschaltmoments
trennen, den niemand in der Datei sieht. Das ist Rekonstruktion, kein Audit.

Zwei Felder, weil eines nicht reicht. ``analysis_system_prompt_version`` ist
lesbar und in einem Bericht zitierbar, kann aber veralten — genau so stand
``schema_version`` auf ``"v2"``, während die Zeile v5-Felder trug.
``analysis_system_prompt_hash`` kann nicht veralten, weil er aus dem Text
abgeleitet wird, ist dafür aber unlesbar. Zusammen beweist der eine, dass die
andere nicht lügt.

Der Name trägt ``system`` mit Absicht: der Hash deckt die Systemhälfte ab. Der
Nutzer-Teil entsteht pro Dokument in :func:`format_user_prompt` und wäre in
jeder Zeile ein anderer.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from scripts.litellm_shadow_eval.loader import SUPPORTED_SCHEMA_VERSIONS

from app.analysis.prompts import (
    ACTIVE_PROMPT_SHA256,
    ACTIVE_PROMPT_VERSION,
    ACTIVE_SYSTEM_PROMPT,
    SYSTEM_PROMPT_V1,
)
from app.observability.llm_telemetry import SCHEMA_VERSION, record_llm_call

REPO = Path(__file__).resolve().parents[2]

#: Die fünf Stellen, an denen der Analyse-System-Prompt gesendet wird.
AUFRUFSTELLEN = (
    "app/analysis/ai_control_plane.py",
    "app/integrations/anthropic/provider.py",
    "app/integrations/gemini/provider.py",
    "app/integrations/openai/provider.py",
    "app/integrations/xai/provider.py",
)


# ---------------------------------------------------------------------------
# Der Hash beschreibt den Text — nicht umgekehrt.
# ---------------------------------------------------------------------------


def test_der_hash_gehoert_zum_aktiven_prompt() -> None:
    """Abgeleitet, nicht hinterlegt.

    Eine notierte Prüfsumme wäre wieder nur ein Literal, das jemand nachziehen
    müsste. Dieser Test hält fest, dass sie es nicht ist.
    """
    assert ACTIVE_PROMPT_SHA256 == hashlib.sha256(ACTIVE_SYSTEM_PROMPT.encode("utf-8")).hexdigest()
    assert len(ACTIVE_PROMPT_SHA256) == 64


def test_der_aktive_prompt_ist_eine_benannte_fassung() -> None:
    """Der Zeiger zeigt auf eine Version, nicht auf einen freien Text.

    Solange nur V1 existiert, ist das V1. Kommt eine V2 dazu, wird die
    Umstellung eine Zeile — und dieser Test sagt dann, welche gilt.
    """
    assert ACTIVE_SYSTEM_PROMPT is SYSTEM_PROMPT_V1
    assert ACTIVE_PROMPT_VERSION == "v1"


def test_die_version_ist_nicht_leer_und_kein_platzhalter() -> None:
    assert ACTIVE_PROMPT_VERSION.strip()
    assert ACTIVE_PROMPT_VERSION.lower() not in {"unknown", "none", "tbd", ""}


# ---------------------------------------------------------------------------
# Der Wächter: eine Verkettung würde den Hash entwerten.
# ---------------------------------------------------------------------------


def test_der_aktive_prompt_wird_nirgends_verkettet() -> None:
    """Ein Hash über die Konstante beschreibt nur dann den gesendeten Text,
    wenn niemand etwas davorhängt.

    Heute reichen alle fünf Stellen die Konstante unverändert durch; der
    Unterschied liegt im Transportfeld (``system=``, ``system_instruction=``,
    ``{"role": "system"}``), nicht im Text. Diese Kontrolle hält die
    Eigenschaft, statt sie einmal zu bestätigen: hängt später jemand etwas an,
    wird es rot statt still falsch.
    """
    muster = re.compile(
        r"ACTIVE_SYSTEM_PROMPT.*(\+|\.format\(|%[sd(])|(\+|%[sd(]).*ACTIVE_SYSTEM_PROMPT"
    )
    funde: list[str] = []
    for pfad in (REPO / "app").rglob("*.py"):
        for nummer, zeile in enumerate(pfad.read_text(encoding="utf-8").splitlines(), 1):
            if "ACTIVE_SYSTEM_PROMPT" in zeile and muster.search(zeile):
                funde.append(f"{pfad.relative_to(REPO).as_posix()}:{nummer}  {zeile.strip()}")

    assert not funde, "der gesendete Text weicht vom gehashten ab:\n" + "\n".join(funde)


def test_keine_aufrufstelle_haelt_noch_eine_feste_version() -> None:
    """Wer ``SYSTEM_PROMPT_V1`` direkt importiert, bleibt bei einer Umstellung
    zurück — und die Telemetrie meldete dann eine Version, die dieser Aufruf
    gar nicht benutzt hat."""
    haengengeblieben = [
        pfad
        for pfad in AUFRUFSTELLEN
        if "SYSTEM_PROMPT_V1" in (REPO / pfad).read_text(encoding="utf-8")
    ]

    assert not haengengeblieben, haengengeblieben


def test_alle_fuenf_aufrufstellen_nehmen_den_zeiger() -> None:
    """Die Gegenprobe zum Test darüber: nicht nur keine alte Version, sondern
    tatsächlich die neue."""
    for pfad in AUFRUFSTELLEN:
        text = (REPO / pfad).read_text(encoding="utf-8")
        assert "ACTIVE_SYSTEM_PROMPT" in text, pfad


# ---------------------------------------------------------------------------
# Die geschriebene Zeile.
# ---------------------------------------------------------------------------


def _zeile(tmp_path: Path, **kwargs: Any) -> dict[str, Any]:
    sink = tmp_path / "telemetry.jsonl"
    record_llm_call(
        provider="gemini", model="gemini-3.6-flash", ok=True, latency_ms=1.0, path=sink, **kwargs
    )
    return json.loads(sink.read_text(encoding="utf-8").strip())


def test_die_provenienz_steht_in_der_zeile(tmp_path: Path) -> None:
    zeile = _zeile(
        tmp_path,
        purpose="analysis",
        analysis_system_prompt_version=ACTIVE_PROMPT_VERSION,
        analysis_system_prompt_hash=ACTIVE_PROMPT_SHA256,
    )

    assert zeile["analysis_system_prompt_version"] == "v1"
    assert zeile["analysis_system_prompt_hash"] == ACTIVE_PROMPT_SHA256
    assert zeile["schema_version"] == "v7"


def test_ohne_angabe_bleibt_es_none_und_wird_nicht_v1(tmp_path: Path) -> None:
    """Historische Zeilen und Aufrufe ohne Analyse-Prompt.

    Ein stilles Defaulting auf ``"v1"`` wäre eine rückwirkende Behauptung über
    Läufe, die niemand gemessen hat — und es machte jede spätere Auswertung
    unbrauchbar, weil V1 und „unbekannt" nicht mehr zu trennen wären. Ein
    STT-Aufruf hat gar keinen Analyse-System-Prompt; er darf keinen melden.
    """
    zeile = _zeile(tmp_path, purpose="stt")

    assert zeile["analysis_system_prompt_version"] is None
    assert zeile["analysis_system_prompt_hash"] is None


def test_der_leser_nimmt_v7_an() -> None:
    """Ein Bump ohne diese Zeile beanstandete jede neue Zeile im S6-Harness."""
    assert SCHEMA_VERSION == "v7"
    assert SCHEMA_VERSION in SUPPORTED_SCHEMA_VERSIONS


def test_die_felder_tragen_system_im_namen() -> None:
    """Die Beweisgrenze gehört in den Namen.

    ``analysis_prompt_hash`` hätte sich wie ein Fingerabdruck der gesamten
    Eingabe gelesen. Er deckt die Systemhälfte ab — und lässt so Raum für ein
    späteres ``analysis_user_prompt_hash``, ohne dass ein Name umgedeutet
    werden müsste.
    """
    quelle = (REPO / "app" / "observability" / "llm_telemetry.py").read_text(encoding="utf-8")

    assert '"analysis_system_prompt_version"' in quelle
    assert '"analysis_system_prompt_hash"' in quelle
    assert '"analysis_prompt_hash"' not in quelle, "der zu weite Name darf nicht zurueckkehren"
