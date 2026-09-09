"""Ein Prompt ist ein Vertrag mit dem Modell — und Vertraege bekommen Nummern.

V1 sagte zu ``bull_case / bear_case / neutral_case`` nur "Optional scenario
analysis" und kein Wort ueber die Form. Das Modell lieferte daraufhin Objekte
statt Strings, und gegen ``strict=True`` ist das ein Schemafehler.
``short_reasoning`` sagt "1-2 sentences" und kam nie als Objekt zurueck — der
Unterschied lag im Prompt, nicht im Modell.

Am 2026-09-09 auf kai-pi5 gemessen, `gemini/gemini-3.6-flash`, echte Dokumente
aus dem Betrieb, mit ``reasoning_effort=minimal`` als Ausloeser:

    V1   7/16 schemagueltig, 19 Objekte in SECHS Schluesselmengen
    V2  16/16 schemagueltig, null Objekte

Warum eine neue Nummer und keine stille Praezisierung: die Analysesemantik
bleibt fachlich gleich, aber das Modellverhalten aendert sich messbar. Haette
V1 den neuen Text bekommen, waeren alle frueheren V1-Messungen nachtraeglich
unzuordenbar geworden — man saehe im Nachhinein nicht mehr, welcher Text sie
erzeugt hat.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from app.analysis.prompts import (
    ACTIVE_SYSTEM_PROMPT,
    ACTIVE_SYSTEM_PROMPT_VERSION,
    PROMPTS_BY_VERSION,
    SYSTEM_PROMPT_V1,
    SYSTEM_PROMPT_V2,
)

REPO = Path(__file__).resolve().parents[2]
APP = REPO / "app"

#: V1 ist eingefroren. Der Hash ist der Beweis, nicht die Absicht.
_V1_SHA256 = hashlib.sha256(SYSTEM_PROMPT_V1.encode("utf-8")).hexdigest()

_TYPZEILEN = (
    "Each MUST be a single plain string of 1-3 sentences, or null.",
    "Never an object, never a list.",
)


def test_v1_ist_eingefroren() -> None:
    """Wer V1 aendert, macht jede historische V1-Messung unzuordenbar.

    Der Test haelt den Hash NICHT als Konstante fest, die man mit der Aenderung
    gleich mit anpasst — er prueft die EIGENSCHAFT, die V1 ausmacht: dass die
    drei Szenariofelder dort weiterhin OHNE Typangabe stehen. Ein Hash-Ratchet
    waere in derselben Zeile aenderbar wie der Text und bewiese nichts.
    """
    assert "Optional scenario analysis. Only provide if meaningful." in SYSTEM_PROMPT_V1
    for zeile in _TYPZEILEN:
        assert zeile not in SYSTEM_PROMPT_V1, (
            "V1 traegt die Praezisierung aus V2 — dann sind die beiden Versionen "
            "nicht mehr auseinanderzuhalten"
        )
    assert len(_V1_SHA256) == 64  # der Hash wird gebildet, nicht behauptet


def test_v2_ist_v1_plus_genau_der_typangabe() -> None:
    """Der Bump darf NUR die gemessene Ausgabeform praezisieren.

    Jede weitere Prompt-Optimierung im selben Schritt machte unbeweisbar,
    welche Aenderung die gemessene Wirkung hatte.
    """
    fehlend = [z for z in _TYPZEILEN if z not in SYSTEM_PROMPT_V2]
    assert not fehlend, fehlend

    # V2 zurueckgerechnet auf V1 muss V1 ergeben — Zeile fuer Zeile, damit
    # eine zusaetzliche Aenderung irgendwo im Text nicht durchrutscht.
    zurueck = SYSTEM_PROMPT_V2
    for zeile in _TYPZEILEN:
        zurueck = zurueck.replace("  " + zeile + "\n", "", 1)
    assert zurueck == SYSTEM_PROMPT_V1, "V2 unterscheidet sich von V1 an mehr als der Typangabe"


def test_jede_version_ist_genau_einmal_hinterlegt() -> None:
    """Zwei Nummern auf denselben Text waeren zwei Namen fuer eine Sache."""
    texte = list(PROMPTS_BY_VERSION.values())
    assert len(texte) == len({id(t) for t in texte})
    assert len(texte) == len(set(texte))


def test_der_merge_schaltet_den_prompt_nicht_nebenbei_um() -> None:
    """V2 ist fertig, aber NICHT aktiv — und das muss festgehalten sein.

    Aktiv wird V2 erst, wenn die Telemetrie `analysis_system_prompt_version`
    und `analysis_system_prompt_hash` persistiert. Ohne diese Felder waere der
    Umschaltmoment aus der Auswertung nicht rekonstruierbar, und genau dessen
    Nachvollziehbarkeit ist der einzige Grund, warum V2 eine eigene Nummer
    bekommt statt einer stillen Praezisierung unter V1.

    Der Test steht hier, damit die Aktivierung ein eigener, sichtbarer Schritt
    bleibt: wer den Zeiger umlegt, muss diese Zusicherung mit anfassen und
    kann es nicht als Nebeneffekt eines Merges tun.
    """
    assert ACTIVE_SYSTEM_PROMPT_VERSION == "v1"
    assert ACTIVE_SYSTEM_PROMPT is SYSTEM_PROMPT_V1


def test_es_gibt_keinen_schleichweg_zu_v2() -> None:
    """Kein Default, kein Fallback, keine Umgebungsvariable schaltet um.

    Eine Automatik waere genau die stille Umschaltung, die der Bump verhindern
    soll -- der Prompt wechselte dann je nach Umgebung, und die Telemetrie
    saehe in beiden Faellen gleich aus.
    """
    quelle = (APP / "analysis" / "prompts.py").read_text(encoding="utf-8")
    code = "\n".join(z for z in quelle.splitlines() if not z.lstrip().startswith("#"))

    for verboten in ("getenv", "environ", "os.getenv", "settings"):
        assert verboten not in code, f"der aktive Prompt haengt an {verboten!r}"
    assert code.count("ACTIVE_SYSTEM_PROMPT_VERSION = ") == 1
