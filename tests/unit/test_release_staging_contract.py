"""Was der laufende Dienst aus der Release-Wurzel liest, muss auch dort liegen.

Der Vorfall, gegen den diese Datei steht (2026-09-04, erster Cutover auf
kai-pi5): ``pi_make_release.sh`` stagte ``app config deploy scripts`` plus
``requirements.lock`` und ``pyproject.toml`` -- sonst nichts. ``verify_release``
meldete **gruen**: Baum-Hash, Pfad und Lock-SHA stimmten alle. Der Baum trug
seinen Anspruch; er konnte nur nicht starten. Beim ersten Restart warf schon
``get_settings()``::

    ValidationError: Schema file not found:
    /home/ubuntu/releases/<SHA>/CONFIG_SCHEMA.json

Fuenf Daemons fielen in die Restart-Schleife, ``/health`` ging auf 000.

Startfaehigkeit war also nie Teil der Release-Identitaet -- und genau das ist
die gefaehrliche Variante: nicht ein Release, das durchfaellt, sondern eines,
das jede eingebaute Pruefung besteht und trotzdem tot ist.

Die Korrektur hat zwei Haelften, und diese Datei bewacht beide:

1. Die Wurzel-Artefakte, die ``app/`` ueber ``Path(__file__).parents[2]``
   aufloest, werden gestaged.
2. Der Builder importiert am Ende aus dem VERSIEGELTEN Baum das, was der Dienst
   importiert. Ein Release, das nicht startet, wird nicht ausgeliefert.

Bewusst statisch gegen den Skripttext: einen echten venv zu bauen dauert auf dem
Pi Minuten und braucht Netz. Der Regressionswert liegt darin, dass das Entfernen
einer dieser Zeilen rot wird -- nicht darin, den Bau nachzustellen.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SKRIPT = REPO / "scripts" / "pi_make_release.sh"


@pytest.fixture(scope="module")
def skript() -> str:
    assert SKRIPT.is_file(), f"Builder fehlt: {SKRIPT}"
    return SKRIPT.read_text(encoding="utf-8")


def _staging_dateiliste(text: str) -> list[str]:
    """Die Namen aus der ``for f in … ; do``-Zeile der Staging-Stufe."""
    treffer = re.search(r"^for f in (.+?); do$", text, re.MULTILINE)
    assert treffer, "Staging-Dateiliste im Builder nicht gefunden"
    return treffer.group(1).split()


def _staging_verzeichnisliste(text: str) -> list[str]:
    treffer = re.search(r"^for d in (.+?); do$", text, re.MULTILINE)
    assert treffer, "Staging-Verzeichnisliste im Builder nicht gefunden"
    return treffer.group(1).split()


# Jede dieser Dateien liegt in der Repo-Wurzel und wird zur Laufzeit ueber
# parents[2] gelesen. Wer eine entfernt, baut ein Release, das nicht startet.
@pytest.mark.parametrize(
    ("datei", "leser"),
    [
        ("CONFIG_SCHEMA.json", "app/core/schema_runtime.py"),
        ("DECISION_SCHEMA.json", "app/core/schema_runtime.py"),
    ],
)
def test_wurzel_schema_wird_gestaged(skript: str, datei: str, leser: str) -> None:
    assert datei in _staging_dateiliste(skript), (
        f"{datei} fehlt im Staging — {leser} loest sie ueber parents[2] auf, "
        f"das Release wuerde beim ersten Start werfen"
    )


def test_die_gestagten_schemata_existieren_wirklich() -> None:
    """Eine Staging-Liste, die auf nichts zeigt, beweist nichts."""
    for datei in ("CONFIG_SCHEMA.json", "DECISION_SCHEMA.json"):
        assert (REPO / datei).is_file(), f"{datei} liegt nicht in der Repo-Wurzel"


def test_monitor_verzeichnis_wird_gestaged(skript: str) -> None:
    """``asset_universe`` liest ``monitor/watchlists.yml`` ueber parents[2]."""
    assert "monitor" in _staging_verzeichnisliste(skript)


def test_die_gebaute_spa_wird_gestaged(skript: str) -> None:
    """Ohne ``web/dist`` verschwindet ``/dashboard`` STILL.

    ``app/api/main.py`` mountet sie hinter ``if _spa_dir.is_dir()`` und ueber den
    CWD-relativen Pfad ``web/dist`` — nach dem Cutover ist das CWD die
    Release-Wurzel. Fehlt sie, gibt es weder Fehler noch Log, nur ein Dashboard,
    das nicht mehr da ist.
    """
    assert "web/dist" in skript, "web/dist wird nicht ins Release gestaged"


def test_der_builder_faellt_auf_ein_nicht_startfaehiges_release_durch(skript: str) -> None:
    """Der Smoke-Import ist die sechste Abbruchstufe, nicht nur ein Hinweis."""
    assert "import app.api.main" in skript, "kein Start-Smoke-Test im Builder"
    assert "SMOKE_IMPORT_FAILED" in skript, "Smoke-Test ohne eigenen Fehlergrund"
    smoke = skript.index("SMOKE_IMPORT_FAILED")
    ready = skript.index("RELEASE_READY=")
    assert smoke < ready, (
        "der Smoke-Test steht hinter RELEASE_READY — ein totes Release wuerde als fertig gemeldet"
    )


def test_der_smoke_test_bricht_ab_statt_zu_warnen(skript: str) -> None:
    block = skript[skript.index("SMOKE_IMPORT_FAILED") :]
    assert "exit 1" in block.split("fi", 1)[0], "Smoke-Test ohne exit 1 ist eine Warnung"


def test_der_builder_ist_syntaktisch_gueltig() -> None:
    import shutil
    import subprocess

    if shutil.which("bash") is None:
        pytest.skip("bash nicht verfuegbar")
    assert subprocess.run(["bash", "-n", str(SKRIPT)], check=False).returncode == 0


# ---------------------------------------------------------------------------
# Zweite Haelfte: gestaged ist nicht gleich versiegelt.
#
# Ein Artefakt, das im Release liegt, aber nicht in SEALED_DIRS/SEALED_FILES
# steht, geht nicht in ``release_tree_sha256`` ein. Es duerfte sich danach
# unbemerkt aendern, und ``verify_release`` bliebe gruen -- die Unveraenderlich-
# keit waere fuer genau die Bytes behauptet, die den Start entscheiden.
# ---------------------------------------------------------------------------


def test_die_startkritischen_wurzeldateien_sind_teil_der_identitaet() -> None:
    from app.observability.release_identity import SEALED_FILES

    for datei in ("CONFIG_SCHEMA.json", "DECISION_SCHEMA.json"):
        assert datei in SEALED_FILES, (
            f"{datei} entscheidet ueber den Start, geht aber nicht in den "
            f"Baum-Hash ein — sie duerfte sich im 'unveraenderlichen' Release "
            f"aendern, ohne dass verify_release es merkt"
        )


def test_jedes_gestagte_verzeichnis_ist_auch_versiegelt(skript: str) -> None:
    from app.observability.release_identity import SEALED_DIRS

    for name in _staging_verzeichnisliste(skript):
        assert name in SEALED_DIRS, f"{name}/ wird gestaged, aber nicht gehasht"


def test_die_spa_ist_teil_der_identitaet() -> None:
    """``web/`` traegt im Release ausschliesslich die gebaute SPA."""
    from app.observability.release_identity import SEALED_DIRS

    assert "web" in SEALED_DIRS


def test_der_builder_versiegelt_was_er_hasht(skript: str) -> None:
    """Die ``chmod a-w``-Stufe darf hinter SEALED_* nicht zurueckbleiben."""
    from app.observability.release_identity import SEALED_DIRS, SEALED_FILES

    block = skript[skript.index("6/6 versiegeln") :]
    chmod = block[: block.index("Selbstkontrolle")]
    for name in SEALED_DIRS:
        assert f"$TARGET/{name}" in chmod, f"{name}/ geht in den Hash ein, bleibt aber schreibbar"
    for name in SEALED_FILES:
        assert f"$TARGET/{name}" in chmod, f"{name} geht in den Hash ein, bleibt aber schreibbar"
