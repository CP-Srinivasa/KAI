"""Optionale Extras im Release — und warum sie die Identität mit erweitern müssen.

Der Befund, gegen den diese Datei steht, wurde beim Bau eines Releases
gefunden, das ein freigegebenes Extra tragen sollte: **`pi_make_release.sh`
konnte es gar nicht.** Es installiert ausschließlich `-r requirements.lock`;
ein optionales Extra aus `pyproject.toml` hat dorthin keinen Weg.

Das allein wäre eine fehlende Fähigkeit. Gefährlich wird es durch die zweite
Hälfte: `release_tree_sha256` schließt den venv ausdrücklich aus, und
`requirements_lock_sha256` kennt nur das Lockfile. Zwei Releases mit demselben
Code und demselben Lock, aber verschiedenem venv, wären an genau den beiden
Feldern nicht zu unterscheiden, die zur Unterscheidung da sind — und die
Idempotenz-Prüfung hätte den zweiten Bau als „baum-identisch" abgewiesen und
den **ersten** zurückgegeben. Still, ohne `RELEASE_TREE_MISMATCH`, ohne Hinweis
auf `--rebuild`, mit einem Erfolgs-Echo und dem falschen Inhalt.

Deshalb prüft diese Datei nicht nur, dass Extras installiert werden, sondern
dass sie **sichtbar** werden: im Pfad, in `release.json` und in der
Idempotenz-Entscheidung.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

_BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(_BASH is None, reason="bash interpreter not available")

REPO = Path(__file__).resolve().parents[2]
BUILDER = REPO / "scripts" / "pi_make_release.sh"


def _text() -> str:
    return BUILDER.read_text(encoding="utf-8")


def _code() -> str:
    """Nur der Code, ohne Kommentare — sonst misst ein Test die Prosa.

    Ein naives ``split("#")`` reicht dafür NICHT: in der Shell ist ``#`` auch
    Teil der Parameter-Expansion. ``${DEPENDENCY_PROFILE#core+}`` wäre damit zu
    ``${DEPENDENCY_PROFILE`` verstümmelt worden — und ein Test, der auf diesem
    Rest sucht, meldet ein Fehlen, das es nicht gibt. Genau das ist beim Bau
    dieser Datei passiert.

    Ein Kommentar beginnt am Zeilenanfang oder nach einem Leerzeichen; eine
    Expansion nicht.
    """
    zeilen: list[str] = []
    for zeile in _text().splitlines():
        if zeile.lstrip().startswith("#"):
            zeilen.append("")
            continue
        stelle = zeile.find(" #")
        zeilen.append(zeile[:stelle] if stelle != -1 else zeile)
    return "\n".join(zeilen)


def test_der_builder_ist_syntaktisch_heil() -> None:
    assert _BASH is not None
    fertig = subprocess.run(  # noqa: S603
        [_BASH, "-n", str(BUILDER)], capture_output=True, text=True, check=False
    )
    assert fertig.returncode == 0, fertig.stderr


# ---------------------------------------------------------------------------
# Die Fähigkeit.
# ---------------------------------------------------------------------------


def test_der_builder_kennt_optionale_extras() -> None:
    code = _code()
    assert "--extra)" in code, "ohne dieses Argument ist ein Extra nicht baubar"
    assert "EXTRA_SPECS" in code


def test_die_extras_werden_vor_pip_check_installiert() -> None:
    """Ein Extra, das dem Lockfile widerspricht, darf kein Release werden.

    Nach `pip check` installiert, käme der Widerspruch als versiegelter,
    verifizierbarer Baum heraus — mit sich widersprechenden Abhängigkeiten.
    """
    code = _code()
    install = code.index("pip install $EXTRA_SPECS")
    pruefung = code.index("-m pip check")
    assert install < pruefung, "erst installieren, dann prüfen"


def test_die_versionen_kommen_aus_pyproject_nicht_aus_dem_skript() -> None:
    """Zwei Orte für dieselbe Version wären zwei Wahrheiten."""
    code = _code()
    assert "pyproject.toml" in code
    assert "optional-dependencies" in code
    assert "litellm" not in code, "der Builder kennt keine Paketnamen"
    assert "==1.99" not in code, "und erst recht keine Versionen"


def test_ein_unbekanntes_extra_bricht_ab() -> None:
    """Ein Tippfehler, der still zu einem Release ohne das Paket führt,
    fällt erst auf, wenn die Unit nicht startet."""
    code = _code()
    assert "UNBEKANNTES_EXTRA" in code
    assert "SystemExit(1)" in code


# ---------------------------------------------------------------------------
# Die Sichtbarkeit — der eigentliche Punkt.
# ---------------------------------------------------------------------------


def test_ein_release_mit_extra_steht_neben_einem_ohne() -> None:
    code = _code()
    assert "RELEASE_ID=" in code
    assert 'TARGET="$RELEASES/$RELEASE_ID"' in code, "der Pfad traegt die Unterscheidung"


def test_der_pfad_haengt_an_den_aufgeloesten_paketen_nicht_am_namen() -> None:
    """Sonst trügen dasselbe Extra in zwei Versionen denselben Pfad.

    Dieselbe Logik wie bei `<SHA>-<tree8>`: nicht „was war gemeint", sondern
    „was ist tatsächlich drin".
    """
    code = _code()
    # Die Zuweisung ist mehrzeilig (`printf` mit echtem Zeilenumbruch), deshalb
    # wird die REGION geprueft, nicht eine einzelne Zeile.
    beginn = code.index('EXTRAS_SHA="$(')
    region = code[beginn : beginn + 200]
    assert "$EXTRA_SPECS" in region, "gehasht werden die Specs, nicht die Namen"
    assert "sha256sum" in region
    assert "${EXTRAS_SHA:0:8}" in code, "und der Hash landet im Pfad"


def test_die_release_json_benennt_die_extras() -> None:
    """Ein Feld, das den Unterschied benennt, ist besser als eine Prüfsumme,
    die ihn nur bemerkt."""
    code = _code()
    for feld in ('"extras": [', '"extra_specs": [', '"extras_sha256":'):
        assert feld in code, feld


def test_die_idempotenz_pruefung_sieht_die_extras_an() -> None:
    """Ohne das gäbe der Builder bei gleichem Code den falschen venv zurück."""
    code = _code()
    assert "extras_sha256" in code
    bedingung = next(
        z for z in code.splitlines() if '"$NEW_TREE" = "$OLD_TREE"' in z and "OLD_EXTRAS" in z
    )
    assert "$EXTRAS_SHA" in bedingung, "beide Merkmale muessen stimmen"
    assert "RELEASE_EXTRAS_MISMATCH" in code, "und der Unterschied wird benannt"


def test_gleicher_baum_andere_extras_ist_kein_treffer() -> None:
    """Der gefährliche Fall: Code identisch, venv verschieden.

    Genau hier hätte der Builder vorher „existiert bereits und ist
    baum-identisch" gemeldet und den vorhandenen Pfad zurückgegeben.
    """
    code = _code()
    stelle = code.index("RELEASE_EXTRAS_MISMATCH")
    # Bis zum Ende des `if`-Blocks: der Abbruch steht hinter den Erklaerzeilen.
    block = code[stelle : code.index("RELEASE_TREE_MISMATCH", stelle)]
    assert "exit 1" in block, "der Bau bricht ab, statt still den alten zu liefern"
    assert 'echo "$TARGET"' not in block, "und gibt keinen Pfad als Erfolg zurueck"


# ---------------------------------------------------------------------------
# Die Annahme steht neben ihrer Voraussetzung.
# ---------------------------------------------------------------------------


def test_der_ausschluss_des_venv_nennt_seine_bedingung() -> None:
    """`Das Lockfile sagt schon alles` gilt nur, solange nichts daneben kommt."""
    quelle = (REPO / "app" / "observability" / "release_identity.py").read_text(encoding="utf-8")
    stelle = quelle.index("EXCLUDED_NAMES")
    kopf = quelle[max(0, stelle - 2000) : stelle]
    assert "VORAUSSETZUNG" in kopf
    assert "ausschliesslich aus dem" in kopf and "Lockfile installiert wird" in kopf
    assert "extra_specs" in kopf, "und der Weg, sie zu erweitern, steht daneben"


def test_der_venv_bleibt_aus_dem_baum_hash_ausgeschlossen() -> None:
    """Die Erweiterung hebt den Ausschluss NICHT auf — sie ergaenzt ihn.

    549 MB bei jedem Health-Lauf zu hashen waere weiterhin teuer und wuerde
    weiterhin nichts beweisen, was `extra_specs` nicht schon sagt.
    """
    from app.observability.release_identity import EXCLUDED_NAMES

    assert ".venv" in EXCLUDED_NAMES


# ---------------------------------------------------------------------------
# Das Extra, um das es geht, existiert wirklich.
# ---------------------------------------------------------------------------


def test_das_litellm_extra_ist_deklariert_und_exakt_gepinnt() -> None:
    manifest = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    extra = manifest["project"]["optional-dependencies"]["litellm"]
    assert extra, "ohne Deklaration koennte --extra litellm nichts installieren"
    for eintrag in extra:
        assert "==" in eintrag, f"nicht exakt gepinnt: {eintrag}"
    assert not any("litellm" in d for d in manifest["project"]["dependencies"])


def test_die_unit_startet_genau_das_binary_das_das_extra_liefert() -> None:
    """Sonst waere das Extra im venv und die Unit trotzdem startunfaehig."""
    unit = (REPO / "deploy" / "systemd" / "kai-litellm.service").read_text(encoding="utf-8")
    assert "/home/kai/current/.venv/bin/litellm" in unit
    manifest = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    extra = " ".join(manifest["project"]["optional-dependencies"]["litellm"])
    assert re.match(r"^litellm\[", extra), extra


# ---------------------------------------------------------------------------
# Das Profil benennt die Abhängigkeitslage — für den Verify-Zeit-Abgleich.
# ---------------------------------------------------------------------------


def test_die_abhaengigkeitslage_hat_einen_namen_keine_liste() -> None:
    """Wer später `verify_release` gegen den venv hält, soll sagen können
    „dieses Release ist ein core+litellm-Env" — statt das aus einer
    Extras-Liste abzuleiten und dabei eine eigene Meinung darüber zu bilden,
    was ein Profil ausmacht.
    """
    code = _code()
    assert 'DEPENDENCY_PROFILE="core"' in code, "ohne Extras ist es schlicht core"
    assert '"dependency_profile": "$DEPENDENCY_PROFILE"' in code
    assert 'DEPENDENCY_PROFILE="core+$' in code


def test_das_profil_steht_vor_jeder_verzweigung() -> None:
    """`set -u` würde sonst beim Bau ohne Extras abbrechen."""
    code = _code()
    zuweisung = code.index('DEPENDENCY_PROFILE="core"')
    erste_nutzung = code.index("$DEPENDENCY_PROFILE")
    assert zuweisung < erste_nutzung


def test_der_pfad_traegt_das_profil_ohne_das_core_praefix() -> None:
    """`<SHA>+core+litellm-<8>` wäre doppelt gemoppelt; `<SHA>+litellm-<8>` reicht."""
    code = _code()
    assert "${DEPENDENCY_PROFILE#core+}" in code


def test_absicht_und_zustand_bleiben_getrennte_felder() -> None:
    """`extras_sha256` sagt, was gewollt war; `dependency_manifest_sha256`,
    was beim Bau tatsächlich installiert wurde.

    Mit nur einem der beiden wüsste man bei einem späteren Rot nicht, ob
    falsch gebaut oder nachträglich verändert wurde.
    """
    code = _code()
    assert '"extras_sha256":' in code
    assert '"dependency_manifest_sha256":' in code
    assert "pip freeze" in code, "das Manifest kommt aus dem tatsaechlichen venv"
