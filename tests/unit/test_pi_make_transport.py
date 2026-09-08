"""Der Transport-Builder — Artefakt und Attestierung, sonst nichts.

ADR 0019 trennt den Abhängigkeitsvertrag des Transports vom Kern, weil
`litellm[proxy]` `openai<3.0.0` verlangt und KAI `openai==3.6.0` fährt. Der
Builder ist der erste Schritt dieser Trennung: er erzeugt einen versiegelten
Baum und attestiert ihn — er installiert keine Unit, startet keinen Dienst und
schaltet keinen Symlink um.

WAS DIESE DATEI PRÜFT UND WAS NICHT

Ein vollständiger Lauf baut ein venv von rund 670 MB. Das gehört nicht in eine
Unit-Suite, die bei jedem Commit läuft. Geprüft wird deshalb, was ohne Bau
entscheidbar ist:

* die **Struktur** des Builders — Reihenfolge, Fail-Pfade, feste Werte
* das **Verhalten** einzelner Fragmente, ausgeführt statt nachgebaut
* die **Idempotenz- und Drift-Logik** gegen echte `transport.json`-Dateien

Was hier NICHT geprüft wird, steht ausdrücklich dabei: ob `litellm` sich
tatsächlich installieren lässt, und ob das Binary auf dieser Plattform läuft.
Beides beantwortet erst ein Lauf auf dem Zielsystem. Ein Test, der so tut, als
hätte er es geprüft, wäre schlimmer als keiner.
"""

from __future__ import annotations

import ast
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(_BASH is None, reason="bash interpreter not available")

NEUZEILE = chr(10)
REPO = Path(__file__).resolve().parents[2]
BUILDER = REPO / "scripts" / "pi_make_transport.sh"


def _text() -> str:
    return BUILDER.read_text(encoding="utf-8")


def _code() -> str:
    """Nur der Code, ohne Kommentare — sonst misst ein Test die Prosa.

    Ein naives ``split("#")`` reicht nicht: in der Shell ist ``#`` auch Teil der
    Parameter-Expansion (``${DEP_MANIFEST:0:8}`` nicht, aber ``${VAR#praefix}``
    sehr wohl). Ein Kommentar beginnt am Zeilenanfang oder nach einem
    Leerzeichen; eine Expansion nicht.
    """
    zeilen: list[str] = []
    for zeile in _text().splitlines():
        if zeile.lstrip().startswith("#"):
            zeilen.append("")
            continue
        stelle = zeile.find(" #")
        zeilen.append(zeile[:stelle] if stelle != -1 else zeile)
    return "\n".join(zeilen)


def _bash(skript: str) -> subprocess.CompletedProcess[str]:
    assert _BASH is not None
    return subprocess.run(  # noqa: S603
        [_BASH, "-c", skript], capture_output=True, text=True, check=False
    )


def test_der_builder_ist_syntaktisch_heil() -> None:
    assert _BASH is not None
    fertig = subprocess.run(  # noqa: S603
        [_BASH, "-n", str(BUILDER)], capture_output=True, text=True, check=False
    )
    assert fertig.returncode == 0, fertig.stderr


# ---------------------------------------------------------------------------
# Die Trennung — der Grund, warum es diesen Builder gibt.
# ---------------------------------------------------------------------------


def test_der_transport_wird_ohne_den_core_lock_installiert() -> None:
    """Das ist der ganze Punkt von ADR 0019.

    Ein `-c requirements.lock` an dieser Stelle holte genau den Konflikt
    zurück, den die Trennung beseitigt: `litellm[proxy]` verlangt
    `openai<3.0.0`, der Core fährt `openai==3.6.0`. Der Transport löst seine
    Abhängigkeiten selbst auf; der Core-Vertrag gilt für den Core.
    """
    code = _code()
    zeile = next(z for z in code.splitlines() if "pip install" in z and "$SPEC" in z)
    assert "-c " not in zeile, zeile
    assert "requirements.lock" not in zeile, zeile


def _pfad_wache(kandidat: Path) -> subprocess.CompletedProcess[str]:
    """Den Pfad-Block AUS dem Builder gegen einen echten Pfad ausfuehren."""
    zeilen = _text().splitlines()
    ab = next(i for i, z in enumerate(zeilen) if z.startswith('TRANSPORTS="$(readlink -f'))
    bis = next(i for i in range(ab, len(zeilen)) if zeilen[i].strip().startswith("mkdir -p"))
    fragment = NEUZEILE.join(zeilen[ab : bis + 1])
    kopf = f'TRANSPORTS="{kandidat.as_posix()}"' + NEUZEILE
    return _bash(kopf + fragment + NEUZEILE + 'printf %s "$TRANSPORTS"')


def test_der_baum_liegt_nicht_unter_releases(tmp_path: Path) -> None:
    """Sonst naehme ihn `pi_activate_release.sh --keep N` beim Aufraeumen mit.

    Ausgefuehrt, nicht gelesen. Die erste Fassung prueft nur, ob die
    Vorgabezeile das Wort `releases` NICHT enthaelt -- eine wahre Aussage ueber
    den Text und keine ueber das Verhalten. Sie war gruen, waehrend die Vorgabe
    `dirname "$REPO"` auf der Pi mit `--repo /home/kai/current` genau nach
    `/home/ubuntu/releases/transport` aufloeste. Die Absicht stand im
    Kommentar, die Ableitung tat etwas anderes, und der Test las die Absicht.
    """
    verboten = tmp_path / "releases" / "transport" / "litellm"
    verboten.mkdir(parents=True)

    fertig = _pfad_wache(verboten)

    assert fertig.returncode == 1, fertig.stdout
    assert "TRANSPORT_PATH_IN_RELEASE_ROTATION" in fertig.stderr


def test_ein_pfad_neben_releases_wird_durchgelassen(tmp_path: Path) -> None:
    """Die Gegenprobe: eine Wache, die alles ablehnt, ist keine."""
    erlaubt = tmp_path / "transport" / "litellm"
    erlaubt.mkdir(parents=True)

    fertig = _pfad_wache(erlaubt)

    assert fertig.returncode == 0, fertig.stderr


def test_die_wache_legt_den_verbotenen_pfad_nicht_an(tmp_path: Path) -> None:
    """Sonst erschuefe jeder Fehlversuch ein Verzeichnis in der Rotation --
    eine Wache, die ihren eigenen Ablehnungsgrund hinterlaesst."""
    verboten = tmp_path / "releases" / "transport" / "litellm"

    fertig = _pfad_wache(verboten)

    assert fertig.returncode == 1
    assert not verboten.exists(), "die Wache hat angelegt, was sie ablehnt"


def test_ein_symlink_in_die_rotation_wird_aufgeloest(tmp_path: Path) -> None:
    """Auf der Pi ist `/home/kai` ein Symlink auf `/home/ubuntu`.

    Ein Pfad kann harmlos aussehen und trotzdem in der Rotation liegen. Ohne
    `pwd -P` urteilte die Wache ueber die Schreibweise statt ueber den Ort.
    """
    echt = tmp_path / "releases" / "transport"
    echt.mkdir(parents=True)
    getarnt = tmp_path / "harmlos"
    try:
        getarnt.symlink_to(echt, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("keine Symlink-Rechte auf dieser Plattform")

    fertig = _pfad_wache(getarnt)

    assert fertig.returncode == 1, fertig.stdout
    assert "TRANSPORT_PATH_IN_RELEASE_ROTATION" in fertig.stderr


def test_die_vorgabe_haengt_am_home_nicht_am_checkout() -> None:
    """`dirname "$REPO"` war der Fehler -- er zeigte auf die Rotation."""
    code = _code()
    vorgabe = next(
        z for z in code.splitlines() if z.strip().startswith("[ -n " + chr(34) + "$TRANSPORTS")
    )
    assert "HOME" in vorgabe, vorgabe
    assert "dirname" not in vorgabe, vorgabe


def test_die_version_kommt_aus_pyproject_nicht_aus_dem_skript() -> None:
    """Zwei Orte für dieselbe Version wären zwei Wahrheiten."""
    code = _code()
    assert "optional-dependencies" in code
    assert "==1.99" not in code, "der Builder kennt keine Versionen"
    assert "UNBEKANNTES_EXTRA" in code, "ein Tippfehler bricht ab"


def test_die_gemeldete_version_kommt_aus_dem_gebauten_venv() -> None:
    """Nicht aus der Spec: gemeldet wird, was installiert IST.

    Eine Spec kann `>=` enthalten oder von pip anders aufgelöst werden. Die
    Version im Manifest muss die tatsächliche sein, sonst behauptet das
    Artefakt etwas über sich, das nicht stimmt.
    """
    code = _code()
    assert "importlib.metadata" in code
    stelle = code.index("VERSION=")
    assert "$SPEC" not in code[stelle : stelle + 200], "die Spec ist nicht die Version"


# ---------------------------------------------------------------------------
# Identität: was den Baum unterscheidbar macht.
# ---------------------------------------------------------------------------


def test_der_pfad_haengt_am_manifest_nicht_nur_an_der_version() -> None:
    """Zwei Bauten derselben Version können verschiedene transitive Auflösungen
    tragen — `litellm[proxy]==1.99.0` pinnt litellm, nicht seine Abhängigkeiten."""
    code = _code()
    ziel = next(z for z in code.splitlines() if z.strip().startswith("TARGET="))
    assert "$VERSION" in ziel and "DEP_MANIFEST" in ziel, ziel


def test_das_manifest_fuehrt_die_geforderten_felder() -> None:
    code = _code()
    for feld in (
        '"schema": "kai_transport/v1"',
        '"transport":',
        '"version":',
        '"spec":',
        '"spec_sha256":',
        '"requirements_lock_sha256":',
        '"dependency_manifest_sha256":',
        '"python_version":',
        '"binary_path":',
        '"builder_version":',
    ):
        assert feld in code, feld


def test_das_manifest_erfindet_keine_bindung_an_einen_kai_commit() -> None:
    """Die Transport-Runtime hat keinen KAI-Commit.

    Ein `repo_sha` dort wäre eine Behauptung über eine Verbindung, die nicht
    existiert — und beim nächsten Vergleich gegen ein Release eine, die
    scheitert.
    """
    assert "repo_sha" not in _code()


def test_der_eigene_lock_wird_aus_dem_gebauten_venv_geschrieben() -> None:
    """Er beschreibt diesen Transport, nicht KAI — und macht den Restore
    netzunabhängig, gerade weil die Pakete in keinem Core-Lock stehen."""
    code = _code()
    assert "pip freeze" in code
    assert '"$STAGE/requirements.lock"' in code


# ---------------------------------------------------------------------------
# Fail-closed: Reihenfolge und Abbruchpfade.
# ---------------------------------------------------------------------------


def test_geprueft_wird_vor_dem_versiegeln() -> None:
    """Nach dem Versiegeln geprüft, käme ein kaputter Baum als fertiges
    Artefakt heraus."""
    code = _code()
    pip_check = code.index("-m pip check")
    versiegeln = code.index('mv "$STAGE" "$TARGET"')
    assert pip_check < versiegeln


def test_der_zielpfad_entsteht_erst_durch_ein_atomares_mv() -> None:
    """Ein abgebrochener Lauf darf keinen halben Baum hinterlassen — sonst
    stünde dort etwas, das aussieht wie ein Artefakt und keines ist."""
    code = _code()
    assert 'mv "$STAGE" "$TARGET"' in code
    assert "trap 'rm -rf \"$STAGE\"' EXIT" in code, "und das Staging wird aufgeraeumt"
    # Vor dem `mv` darf nichts in den Zielpfad geschrieben werden.
    vor_dem_mv = code[: code.index('mv "$STAGE" "$TARGET"')]
    assert '> "$TARGET' not in vor_dem_mv
    assert 'mkdir -p "$TARGET"' not in vor_dem_mv


def test_ein_manifest_mismatch_nach_dem_versiegeln_bricht_ab() -> None:
    """Die Selbstkontrolle ist der Unterschied zwischen „gebaut" und „belegt"."""
    code = _code()
    assert "TRANSPORT_MANIFEST_MISMATCH" in code
    stelle = code.index("TRANSPORT_MANIFEST_MISMATCH")
    assert "_verwerfen" in code[stelle : stelle + 400], "abbrechen UND den Baum wegnehmen"


def test_ein_nachtraeglich_veraenderter_baum_wird_nicht_wiederverwendet() -> None:
    """Tamper-Fall: der Baum trägt sein eigenes Manifest nicht mehr.

    Ein Builder, der ihn stillschweigend zurückgäbe, machte aus einer
    Veränderung ein Ergebnis.
    """
    code = _code()
    assert "TRANSPORT_DEPENDENCY_DRIFT" in code
    stelle = code.index("TRANSPORT_DEPENDENCY_DRIFT")
    block = code[stelle : stelle + 600]
    assert "exit 1" in block
    assert 'echo "$VORHANDEN"' not in block, "und wird nicht als Erfolg gemeldet"


def test_ein_fehlendes_binary_bricht_ab() -> None:
    code = _code()
    assert "TRANSPORT_BINARY_MISSING" in code


def test_die_startfaehigkeit_wird_geprueft_ohne_einen_dienst_zu_starten() -> None:
    """Ein Baum, der sich versiegeln lässt und beim ersten Start wirft, ist die
    gefährlichste Variante — beim Release ist genau das am 2026-09-04 passiert.

    Geprüft wird mit `--version`: kein Server, kein Port, kein Zustand.
    """
    code = _code()
    assert "TRANSPORT_SMOKE_FAILED" in code
    smoke = next(z for z in code.splitlines() if "TRANSPORT_SMOKE_FAILED" in z or "--version" in z)
    assert "--version" in code
    assert "--port" not in code, "der Builder startet keinen Server"
    assert "--host" not in code
    del smoke


# ---------------------------------------------------------------------------
# Der Builder installiert nichts und startet nichts.
# ---------------------------------------------------------------------------


def test_der_builder_fasst_weder_units_noch_symlinks_noch_dienste_an() -> None:
    """ADR 0019: Artefakt und Attestierung, alles Weitere sind getrennte Tore."""
    code = _code()
    for verboten in (
        "systemctl",
        "/etc/systemd",
        "daemon-reload",
        "kai-service-control",
        "ln -s",
        "deployment_marker",
        "sudo",
    ):
        assert verboten not in code, verboten


def test_keine_geheimnisse_im_artefakt() -> None:
    """Und die Prüfung schließt den venv aus.

    In Paket-Metadaten stehen fremde Beispielschlüssel; ein Fehlalarm darüber
    würde die Prüfung entwerten, statt sie zu schärfen.
    """
    code = _code()
    assert "SECRET_IN_ARTIFACT" in code
    zeile = next(z for z in code.splitlines() if "grep -rlE" in z)
    assert "--exclude-dir=.venv" in code, zeile
    assert ".env" in code


# ---------------------------------------------------------------------------
# Verhalten, ausgeführt statt nachgebaut.
# ---------------------------------------------------------------------------


def _spec_sha(specs: str) -> str:
    """Die Spec-Hash-Zeile AUS dem Builder ausführen."""
    zeilen = _text().splitlines()
    fragment = next(z.strip() for z in zeilen if z.strip().startswith("SPEC_SHA="))
    fertig = _bash(f'SPEC="{specs}"\n{fragment}\nprintf %s "$SPEC_SHA"')
    assert fertig.returncode == 0, fertig.stderr
    return fertig.stdout.strip()


def test_gleiche_spec_ergibt_denselben_spec_hash() -> None:
    a = _spec_sha("litellm[proxy]==1.99.0")
    b = _spec_sha("litellm[proxy]==1.99.0")
    assert a == b
    assert len(a) == 64


def test_die_reihenfolge_der_specs_aendert_den_hash_nicht() -> None:
    assert _spec_sha("aaa==1 bbb==2") == _spec_sha("bbb==2 aaa==1")


def test_eine_andere_version_ergibt_einen_anderen_spec_hash() -> None:
    assert _spec_sha("litellm[proxy]==1.99.0") != _spec_sha("litellm[proxy]==2.0.0")


def _idempotenz_probe(wurzel: Path, spec_sha: str) -> str:
    """Die Suchlogik AUS dem Builder gegen echte Dateien ausfuehren.

    Zeilenbasiert geschnitten, nicht an einer Zeichenposition: die Zuweisung
    ist mehrzeilig und endet mit ``|| VORHANDEN=""``. Ein Index-Schnitt verlor
    beim ersten Versuch das schliessende Quote, und der Test meldete einen
    Syntaxfehler, den es im Builder nicht gab -- ein Pruefer, der seinen
    Gegenstand vorher beschaedigt.
    """
    zeilen = _text().splitlines()
    start = next(i for i, z in enumerate(zeilen) if z.strip().startswith('VORHANDEN="$(python3'))
    ende = next(i for i in range(start, len(zeilen)) if 'VORHANDEN=""' in zeilen[i])
    fragment = "\n".join(zeilen[start : ende + 1])

    vorspann = f'TRANSPORTS="{wurzel.as_posix()}"' + "\n" + f'SPEC_SHA="{spec_sha}"' + "\n"
    fertig = _bash(vorspann + fragment + "\n" + 'printf %s "$VORHANDEN"')
    assert fertig.returncode == 0, fertig.stderr + "\n---\n" + fragment
    return fertig.stdout.strip()


def _baum(wurzel: Path, name: str, spec_sha: str) -> Path:
    ziel = wurzel / name
    ziel.mkdir(parents=True)
    (ziel / "transport.json").write_text(
        json.dumps({"spec_sha256": spec_sha, "dependency_manifest_sha256": "x" * 64}),
        encoding="utf-8",
    )
    return ziel


def test_die_probe_findet_einen_baum_mit_derselben_spec(tmp_path: Path) -> None:
    """Sonst baute jeder Lauf 670 MB neu, nur um sie wegzuwerfen."""
    erwartet = _baum(tmp_path, "1.99.0-aabbccdd", "a" * 64)
    _baum(tmp_path, "1.98.0-11223344", "b" * 64)

    assert Path(_idempotenz_probe(tmp_path, "a" * 64)) == erwartet


def test_die_probe_findet_nichts_bei_anderer_spec(tmp_path: Path) -> None:
    """Eine geänderte Spec ist ein anderer Transport — der erste Bau gewinnt
    nur für DIESELBE Spec."""
    _baum(tmp_path, "1.99.0-aabbccdd", "a" * 64)

    assert _idempotenz_probe(tmp_path, "c" * 64) == ""


def test_die_probe_uebergeht_einen_unlesbaren_baum(tmp_path: Path) -> None:
    """Ein kaputtes Manifest darf die Suche nicht abbrechen — sonst
    verhinderte ein einzelner Rest jeden weiteren Bau."""
    kaputt = tmp_path / "1.97.0-deadbeef"
    kaputt.mkdir()
    (kaputt / "transport.json").write_text("{kein json", encoding="utf-8")
    erwartet = _baum(tmp_path, "1.99.0-aabbccdd", "a" * 64)

    assert Path(_idempotenz_probe(tmp_path, "a" * 64)) == erwartet


def test_die_probe_ist_leer_wenn_es_noch_nichts_gibt(tmp_path: Path) -> None:
    assert _idempotenz_probe(tmp_path, "a" * 64) == ""


def test_ein_nach_dem_versiegeln_gescheiterter_baum_bleibt_nicht_liegen() -> None:
    """Sonst traegt er seinen endgueltigen Namen und ein gueltiges Manifest.

    Der naechste Lauf faende ihn ueber die Idempotenz-Suche und meldete ihn als
    fertig -- ein Transport, dessen Binary nie gestartet ist, wieder in Betrieb
    genommen, weil er von aussen aussieht wie einer, der laeuft. Das atomare
    `mv` schuetzt nur den Weg BIS zum Namen; danach muss der Baum aktiv
    verworfen werden.
    """
    code = _code()
    # Erst NACH der mv-Zeile: deren eigenes `exit 1` gilt dem gescheiterten mv,
    # und dann steht am Zielpfad nichts, was liegen bleiben koennte.
    zeilen = code.splitlines()
    ab = next(i for i, z in enumerate(zeilen) if 'mv "$STAGE" "$TARGET"' in z) + 1
    danach = NEUZEILE.join(zeilen[ab:])

    assert danach.count("_verwerfen") == 2, "beide Kontrollen verwerfen"
    assert "exit 1" not in danach, "kein blankes exit -- das liesse den Baum stehen"
    assert "exit 0" in danach, "der Erfolgsweg bleibt"


def test_die_suche_uebergeht_verworfene_baeume(tmp_path: Path) -> None:
    """Die Quarantaene liegt eine Ebene tiefer, das Muster `*/transport.json`
    reicht nur eine Ebene weit. Ausgefuehrt statt behauptet: dass die Ablage
    unsichtbar IST, entscheidet das Suchmuster, nicht die Absicht."""
    verworfen = tmp_path / "rejected" / "1.99.0-aabbccdd.4711"
    verworfen.mkdir(parents=True)
    (verworfen / "transport.json").write_text(
        json.dumps({"spec_sha256": "a" * 64, "dependency_manifest_sha256": "d" * 64}),
        encoding="utf-8",
    )

    assert _idempotenz_probe(tmp_path, "a" * 64) == ""


# ---------------------------------------------------------------------------
# Was hier NICHT geprüft wird.
# ---------------------------------------------------------------------------


def _quelle_ohne_diese_zusage(name: str) -> str:
    """Der Pruefer darf sich nicht selbst als Treffer zaehlen.

    Die erste Fassung suchte die verbotenen Zeichenketten im ganzen Dateitext
    -- und fand sie in ihren eigenen Zusicherungen. Dieselbe Klasse wie ein
    ``pgrep -f``, das seine eigene Kommandozeile sieht: der Pruefer ist Teil
    des Pruefgegenstands geworden.
    """
    quelle = Path(__file__).read_text(encoding="utf-8")
    zeilen = quelle.splitlines()
    for knoten in ast.parse(quelle).body:
        if isinstance(knoten, ast.FunctionDef) and knoten.name == name:
            assert knoten.end_lineno is not None
            del zeilen[knoten.lineno - 1 : knoten.end_lineno]
            return NEUZEILE.join(zeilen)
    raise AssertionError(f"{name} wurde umbenannt -- die Zusage haengt in der Luft")


def test_diese_datei_baut_kein_venv() -> None:
    """Eine ausdrueckliche Zusage, damit niemand sie fuer einen Vollnachweis haelt.

    Ob `litellm[proxy]==1.99.0` sich installieren laesst und ob das Binary auf
    aarch64 laeuft, beantwortet erst ein Lauf auf dem Zielsystem. Diese Suite
    prueft Struktur und Fragmente -- nicht das Ergebnis eines Baus.
    """
    quelle = _quelle_ohne_diese_zusage("test_diese_datei_baut_kein_venv")
    assert "pi_make_transport" + ".sh --repo" not in quelle, "kein Vollauf des Builders"
    assert "python3 -m " + "venv" not in quelle, "diese Suite legt kein venv an"
    # Nicht auf "pip install" pruefen: die Zeichenkette steht hier zu Recht --
    # ein anderer Test SUCHT sie im Builder. Eine Erwaehnung ist keine
    # Ausfuehrung, und ein Pruefer, der beides verwechselt, prueft nichts.
    assert quelle.count("str(BUILDER)") == 1, "der Builder laeuft nur unter `bash -n`"
