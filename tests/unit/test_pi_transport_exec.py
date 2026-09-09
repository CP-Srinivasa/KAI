"""Der Transport-Verifizierer — die Unit startet einen ATTESTIERTEN Baum oder keinen.

ADR 0019 nimmt das LiteLLM-Binary aus dem Release-venv heraus. Damit braucht die
Unit einen Weg dorthin, der nicht "irgendein litellm" startet: kein PATH, keine
Umgebungsvariable, kein Checkout-venv. Diese Datei fuehrt das Skript gegen echte
Baeume aus — jeder Fehlerpfad einzeln, und der Erfolgsweg bis zum ``exec``.

Die eine Konstante, die dafuer ersetzt wird, ist der fest verdrahtete
Wurzelpfad. Dass er im ausgelieferten Skript auf ``/home/kai/transport`` steht
und NICHT aus der Umgebung kommt, prueft ein eigener Test — sonst waere die
Ersetzung genau das Loch, das die Attestierung schliessen soll.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

_BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(_BASH is None, reason="bash interpreter not available")

REPO = Path(__file__).resolve().parents[2]
SKRIPT = REPO / "scripts" / "pi_transport_exec.sh"
NEUZEILE = chr(10)


def _schreibe(pfad: Path, text: str) -> None:
    """Immer LF, nie CRLF.

    ``write_text`` uebersetzt unter Windows nach CRLF. Ein Shell-Skript mit
    CRLF laeuft anders, und ein ``pip freeze``-Ergebnis mit CRLF hat einen
    anderen sha256 als dasselbe Ergebnis mit LF -- der Test misst dann eine
    Abweichung, die es auf dem Zielsystem nicht gibt.
    """
    pfad.write_text(text, encoding="utf-8", newline=NEUZEILE)


def _ausfuehrbar(pfad: Path) -> None:
    pfad.chmod(pfad.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _skript_mit_wurzel(tmp_path: Path, wurzel: Path) -> Path:
    """Das ECHTE Skript, nur mit ersetztem Wurzelpfad."""
    quelle = SKRIPT.read_text(encoding="utf-8")
    marke = 'TRANSPORTS_ROOT="/home/kai/transport"'
    assert marke in quelle, "der Wurzelpfad steht nicht mehr, wo der Test ihn ersetzt"
    py_marke = 'PY_SYSTEM="/usr/bin/python3"'
    assert py_marke in quelle, "der Interpreter steht nicht mehr, wo der Test ihn ersetzt"
    ziel = tmp_path / "pi_transport_exec.sh"
    _schreibe(
        ziel,
        quelle.replace(marke, f'TRANSPORTS_ROOT="{wurzel.as_posix()}"').replace(
            py_marke, f'PY_SYSTEM="{Path(sys.executable).as_posix()}"'
        ),
    )
    _ausfuehrbar(ziel)
    return ziel


def _baum(
    wurzel: Path,
    *,
    name: str = "litellm",
    freeze: str = "aaa==1" + chr(10) + "bbb==2" + chr(10),
    manifest_hash: str | None = None,
    shebang: str | None = None,
    binary_path: str | None = None,
    transport_name: str | None = None,
    rumpf: str | None = None,
) -> Path:
    """Ein Baum, wie ihn ``pi_make_transport.sh`` hinterlaesst.

    ``current`` ist hier ein echtes Verzeichnis, kein Symlink: ``readlink -f``
    loest beides auf, und Symlinks brauchen unter Windows Sonderrechte. Dass ein
    Symlink aufgeloest wird, prueft ein eigener Test.
    """
    baum = wurzel / name / "current"
    binaer = baum / ".venv" / "bin"
    binaer.mkdir(parents=True)

    # Ein Interpreter, der auf `-m pip freeze` antwortet — mehr braucht die
    # Pruefung vom venv nicht.
    py = binaer / "python3"
    # `%b` und nicht `%s`: nur `%b` loest die Escapes auf. Mit `%s` stuende ein
    # literales Backslash-n in der Ausgabe, und der Hash waere ein anderer als
    # der, den ein echtes `pip freeze` erzeugt.
    _schreibe(py, "#!/bin/sh" + NEUZEILE + f"printf %b {json.dumps(freeze)}" + NEUZEILE)
    _ausfuehrbar(py)

    echt = hashlib.sha256(
        (NEUZEILE.join(sorted(freeze.splitlines())) + NEUZEILE).encode()
    ).hexdigest()

    bin_datei = binaer / name
    _schreibe(
        bin_datei,
        (shebang or "#!/bin/sh") + NEUZEILE + (rumpf or 'echo "GESTARTET $*"') + NEUZEILE,
    )
    _ausfuehrbar(bin_datei)

    _schreibe(
        baum / "transport.json",
        json.dumps(
            {
                "schema": "kai_transport/v1",
                "transport": transport_name or name,
                "version": "1.99.0",
                "binary_path": bin_datei.as_posix() if binary_path is None else binary_path,
                "dependency_manifest_sha256": manifest_hash or echt,
            }
        ),
    )
    return baum


def _lauf(
    skript: Path, *args: str, umgebung: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    assert _BASH is not None
    return subprocess.run(  # noqa: S603
        [_BASH, str(skript), *args],
        capture_output=True,
        text=True,
        check=False,
        env=umgebung,
    )


def test_das_skript_ist_syntaktisch_heil() -> None:
    assert _BASH is not None
    fertig = subprocess.run(  # noqa: S603
        [_BASH, "-n", str(SKRIPT)], capture_output=True, text=True, check=False
    )
    assert fertig.returncode == 0, fertig.stderr


# ---------------------------------------------------------------------------
# Der Erfolgsweg.
# ---------------------------------------------------------------------------


def test_ein_attestierter_baum_wird_gestartet_und_bekommt_seine_argumente(
    tmp_path: Path,
) -> None:
    """Bis zum ``exec``, nicht bis zur letzten Pruefung."""
    wurzel = tmp_path / "transport"
    _baum(wurzel)
    skript = _skript_mit_wurzel(tmp_path, wurzel)

    fertig = _lauf(skript, "litellm", "--host", "127.0.0.1", "--port", "4000")

    assert fertig.returncode == 0, fertig.stderr
    assert "GESTARTET --host 127.0.0.1 --port 4000" in fertig.stdout
    assert "TRANSPORT_VERIFIED" in fertig.stderr


def test_die_provenienz_steht_im_log(tmp_path: Path) -> None:
    """Wer spaeter fragt, welcher Baum lief, soll es lesen koennen."""
    wurzel = tmp_path / "transport"
    _baum(wurzel)
    skript = _skript_mit_wurzel(tmp_path, wurzel)

    fertig = _lauf(skript, "litellm")

    assert "name=litellm" in fertig.stderr
    assert "version=1.99.0" in fertig.stderr
    assert "manifest=" in fertig.stderr


# ---------------------------------------------------------------------------
# Fail-closed: jeder Pfad einzeln.
# ---------------------------------------------------------------------------


def test_ohne_installierten_transport_startet_nichts(tmp_path: Path) -> None:
    skript = _skript_mit_wurzel(tmp_path, tmp_path / "leer")

    fertig = _lauf(skript, "litellm")

    assert fertig.returncode == 1
    assert "TRANSPORT_NOT_INSTALLED" in fertig.stderr
    assert "GESTARTET" not in fertig.stdout


def test_ein_veraenderter_baum_startet_nicht(tmp_path: Path) -> None:
    """Der Baum ist nicht mehr der attestierte — das ist der Kern der Sache."""
    wurzel = tmp_path / "transport"
    _baum(wurzel, manifest_hash="d" * 64)
    skript = _skript_mit_wurzel(tmp_path, wurzel)

    fertig = _lauf(skript, "litellm")

    assert fertig.returncode == 1
    assert "TRANSPORT_DEPENDENCY_DRIFT" in fertig.stderr
    assert "GESTARTET" not in fertig.stdout


def test_ein_manifest_das_woanders_hinzeigt_wird_abgewiesen(tmp_path: Path) -> None:
    """Ein kopiertes Manifest beschreibt nicht, was hier liegt."""
    wurzel = tmp_path / "transport"
    _baum(wurzel, binary_path="/usr/bin/env")
    skript = _skript_mit_wurzel(tmp_path, wurzel)

    fertig = _lauf(skript, "litellm")

    assert fertig.returncode == 1
    assert "TRANSPORT_MANIFEST_FOREIGN" in fertig.stderr


def test_ein_fremder_transportname_wird_abgewiesen(tmp_path: Path) -> None:
    wurzel = tmp_path / "transport"
    _baum(wurzel, transport_name="etwas-anderes")
    skript = _skript_mit_wurzel(tmp_path, wurzel)

    fertig = _lauf(skript, "litellm")

    assert fertig.returncode == 1
    assert "TRANSPORT_NAME_MISMATCH" in fertig.stderr


def test_eine_shebang_ins_leere_faellt_vorher_auf(tmp_path: Path) -> None:
    """Genau der Befund vom 2026-09-08.

    ``pip`` backt den absoluten Interpreterpfad in jede Konsolen-Anwendung; nach
    einem Verschieben zeigte er ins Staging, das es nicht mehr gab. ``execve``
    meldet dann "No such file or directory" ueber eine Datei, die existiert —
    eine Meldung, die in die Irre fuehrt. Hier faellt es mit Namen auf.
    """
    wurzel = tmp_path / "transport"
    _baum(wurzel, shebang="#!/gibt/es/nicht/python3")
    skript = _skript_mit_wurzel(tmp_path, wurzel)

    fertig = _lauf(skript, "litellm")

    assert fertig.returncode == 1
    assert "TRANSPORT_INTERPRETER_MISSING" in fertig.stderr


def test_eine_shebang_in_einen_anderen_baum_faellt_auf(tmp_path: Path) -> None:
    """Existiert, ist ausfuehrbar — und gehoert zu einem anderen Transport."""
    wurzel = tmp_path / "transport"
    fremd = wurzel / "anderer" / "current" / ".venv" / "bin"
    fremd.mkdir(parents=True)
    fremder_py = fremd / "python3"
    _schreibe(fremder_py, "#!/bin/sh" + NEUZEILE + "true" + NEUZEILE)
    _ausfuehrbar(fremder_py)
    _baum(wurzel, shebang=f"#!{fremder_py.as_posix()}")
    skript = _skript_mit_wurzel(tmp_path, wurzel)

    fertig = _lauf(skript, "litellm")

    assert fertig.returncode == 1
    assert "TRANSPORT_INTERPRETER_FOREIGN" in fertig.stderr


def test_ein_unlesbares_manifest_startet_nichts(tmp_path: Path) -> None:
    wurzel = tmp_path / "transport"
    baum = _baum(wurzel)
    _schreibe(baum / "transport.json", "{kein json")
    skript = _skript_mit_wurzel(tmp_path, wurzel)

    fertig = _lauf(skript, "litellm")

    assert fertig.returncode == 1
    assert "TRANSPORT_MANIFEST_UNREADABLE" in fertig.stderr


def test_ohne_transportnamen_bricht_der_aufruf_ab(tmp_path: Path) -> None:
    """Ein leerer Name duerfte nie zu einem Pfad werden, der zufaellig passt."""
    skript = _skript_mit_wurzel(tmp_path, tmp_path / "transport")

    fertig = _lauf(skript)

    assert fertig.returncode == 2


@pytest.mark.skipif(os.name == "nt", reason="Symlinks brauchen unter Windows Sonderrechte")
def test_ein_symlink_wird_aufgeloest_und_der_echte_baum_geprueft(tmp_path: Path) -> None:
    """``current`` ist im Betrieb ein Zeiger. Geprueft wird, worauf er zeigt."""
    wurzel = tmp_path / "transport"
    echt = _baum(wurzel)
    ziel = wurzel / "litellm" / "1.99.0-abcd1234"
    echt.rename(ziel)
    (wurzel / "litellm" / "current").symlink_to(ziel, target_is_directory=True)

    # Der Builder schreibt den AUFGELOESTEN Binaerpfad ins Manifest, nie einen
    # ueber `current`. Ein Manifest, das ueber den Zeiger auf sich selbst
    # verweist, wird zu Recht als fremd abgewiesen -- das Fixture muss dem
    # echten Baum entsprechen, sonst prueft der Test seine eigene Bastelei.
    manifest = json.loads((ziel / "transport.json").read_text(encoding="utf-8"))
    manifest["binary_path"] = (ziel / ".venv" / "bin" / "litellm").as_posix()
    _schreibe(ziel / "transport.json", json.dumps(manifest))
    skript = _skript_mit_wurzel(tmp_path, wurzel)

    fertig = _lauf(skript, "litellm")

    assert fertig.returncode == 0, fertig.stderr
    assert "1.99.0-abcd1234" in fertig.stderr, "der aufgeloeste Baum gehoert ins Log"


# ---------------------------------------------------------------------------
# Was das Skript NICHT tun darf.
# ---------------------------------------------------------------------------


def test_der_wurzelpfad_steht_fest_und_kommt_nicht_aus_der_umgebung() -> None:
    """Sonst waere die Ersetzung im Test das Loch, das die Attestierung schliesst.

    Eine Variable, die den Wurzelpfad verschieben koennte, ist genau der Weg,
    einen anderen Prozess unter demselben Namen zu starten.
    """
    quelle = SKRIPT.read_text(encoding="utf-8")
    assert 'TRANSPORTS_ROOT="/home/kai/transport"' in quelle
    assert "${TRANSPORTS_ROOT:-" not in quelle
    assert "$KAI_TRANSPORT" not in quelle
    # Derselbe Grund fuer den Interpreter: er liest das Manifest, das ueber
    # Start oder Nicht-Start entscheidet. Aus dem PATH waere er austauschbar.
    assert 'PY_SYSTEM="/usr/bin/python3"' in quelle
    assert "${PY_SYSTEM:-" not in quelle


def test_das_binary_kommt_nie_aus_dem_pfad_oder_dem_release_venv() -> None:
    """Der ganze Grund, warum es dieses Skript gibt."""
    quelle = SKRIPT.read_text(encoding="utf-8")
    code = NEUZEILE.join(z for z in quelle.splitlines() if not z.lstrip().startswith("#"))

    assert "which " not in code
    assert "command -v" not in code
    assert "/home/kai/current/.venv" not in code
    assert code.count("exec ") == 1, "genau ein exec, und der geht ans attestierte Binary"


def test_das_skript_installiert_und_startet_nichts_anderes() -> None:
    """Keine Unit, kein systemd, kein sudo — hier wird geprueft und exec't."""
    quelle = SKRIPT.read_text(encoding="utf-8")
    for verboten in ("systemctl", "sudo", "daemon-reload", "pip install", "ln -s"):
        assert verboten not in quelle, verboten


# ---------------------------------------------------------------------------
# Die Geheimnisse der Anbieter — von der EnvironmentFile bis in den Transport.
# ---------------------------------------------------------------------------
#
# Am 2026-09-09 lautete der Verdacht, `GEMINI_API_KEY` gehe auf dem Weg
# systemd -> runtime-exec -> dieses Skript -> LiteLLM verloren. Er tat es nicht:
# auf kai-pi5 lag der Schluessel byte-identisch im laufenden Prozess. Die
# Fehlmessung entstand daneben, nicht in der Kette.
#
# Die Kette war also richtig und war nirgends festgehalten. Genau das schliesst
# dieser Abschnitt: nicht Gemini, sondern die KLASSE. Das Skript exec't, und
# `exec` vererbt die Umgebung vollstaendig — ein spaeter eingezogener
# "Sanitizer" waere still, weil ein Proxy ohne Schluessel erst beim ersten
# echten Aufruf auffaellt, nicht beim Start.

#: Anbieter-Geheimnisse, die den Transportprozess erreichen muessen, plus der
#: Proxy-Schluessel. Keine Gemini-Sonderlocke: wer eine Route auf einen neuen
#: Anbieter stellt, traegt dessen Namen hier ein.
_GEHEIMNISSE = (
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "XAI_API_KEY",
    "LITELLM_MASTER_KEY",
)

#: Der Rumpf meldet ausschliesslich VORHANDENSEIN. Ein Test, der Werte ausgibt,
#: schriebe sie ins CI-Protokoll und waere selbst das Leck, das er sucht.
_MELDET_PRAESENZ = NEUZEILE.join(
    f'if [ -n "${{{name}:-}}" ]; then echo "{name}=SET"; else echo "{name}=MISSING"; fi'
    for name in _GEHEIMNISSE
)

#: Erkennbar, aber kein echtes Schluesselformat.
_WERT = {name: f"probe-value-for-{name.lower()}" for name in _GEHEIMNISSE}


def _umgebung_mit_geheimnissen() -> dict[str, str]:
    umgebung = dict(os.environ)
    umgebung.update(_WERT)
    return umgebung


def _umgebung_ohne_geheimnisse() -> dict[str, str]:
    umgebung = dict(os.environ)
    for name in _GEHEIMNISSE:
        umgebung.pop(name, None)
    return umgebung


def test_die_anbieter_geheimnisse_erreichen_den_transportprozess(tmp_path: Path) -> None:
    """Der Beweis, den der Verdacht vom 2026-09-09 verlangt hat — als Kontrolle."""
    wurzel = tmp_path / "transport"
    _baum(wurzel, rumpf=_MELDET_PRAESENZ)
    skript = _skript_mit_wurzel(tmp_path, wurzel)

    fertig = _lauf(skript, "litellm", umgebung=_umgebung_mit_geheimnissen())

    assert fertig.returncode == 0, fertig.stderr
    for name in _GEHEIMNISSE:
        assert f"{name}=SET" in fertig.stdout, f"{name} erreicht den Transport nicht"


def test_ein_fehlendes_geheimnis_wird_nicht_erfunden(tmp_path: Path) -> None:
    """Kein Platzhalter, kein Leerstring, keine Vorgabe aus dem Skript.

    Ein erfundener Wert waere schlimmer als ein fehlender: der Proxy startete,
    und der Fehler zeigte sich erst als Anbieter-Ablehnung mitten im Betrieb.
    """
    wurzel = tmp_path / "transport"
    _baum(wurzel, rumpf=_MELDET_PRAESENZ)
    skript = _skript_mit_wurzel(tmp_path, wurzel)

    fertig = _lauf(skript, "litellm", umgebung=_umgebung_ohne_geheimnisse())

    assert fertig.returncode == 0, fertig.stderr
    for name in _GEHEIMNISSE:
        assert f"{name}=MISSING" in fertig.stdout, f"{name} wurde erfunden"


def test_die_kontrolle_schlaegt_bei_einem_eingezogenen_sanitizer_fehl(tmp_path: Path) -> None:
    """Gegenprobe: veraenderte Kulisse, sonst prueft der Nachweis oben nichts.

    Hier bekommt das Skript genau die Zeile, die ein gut gemeinter Sanitizer
    einzoege. Meldet die Sonde dann immer noch SET, misst sie nicht die
    Weitergabe, sondern ihre eigene Umgebung.
    """
    wurzel = tmp_path / "transport"
    _baum(wurzel, rumpf=_MELDET_PRAESENZ)
    skript = _skript_mit_wurzel(tmp_path, wurzel)

    quelle = skript.read_text(encoding="utf-8")
    marke = 'exec "$BINARY" "$@"'
    assert marke in quelle, "der exec steht nicht mehr, wo die Gegenprobe ihn ersetzt"
    _schreibe(
        skript,
        quelle.replace(marke, "unset " + " ".join(_GEHEIMNISSE) + NEUZEILE + marke),
    )

    fertig = _lauf(skript, "litellm", umgebung=_umgebung_mit_geheimnissen())

    assert fertig.returncode == 0, fertig.stderr
    for name in _GEHEIMNISSE:
        assert f"{name}=MISSING" in fertig.stdout, (
            f"{name} ueberlebte ein `unset` — die Sonde misst nicht die Weitergabe"
        )


def test_kein_geheimnis_steht_im_protokoll_des_transports(tmp_path: Path) -> None:
    """Das Skript schreibt Provenienz, nicht Umgebung.

    Die Ausgabe landet per ``StandardError=append:`` in einer Logdatei, die
    Backups und Diagnosen mitnehmen. Ein Wert darin waere dauerhaft.
    """
    wurzel = tmp_path / "transport"
    _baum(wurzel, rumpf=_MELDET_PRAESENZ)
    skript = _skript_mit_wurzel(tmp_path, wurzel)

    fertig = _lauf(skript, "litellm", umgebung=_umgebung_mit_geheimnissen())

    assert "TRANSPORT_VERIFIED" in fertig.stderr
    for name, wert in _WERT.items():
        assert wert not in fertig.stdout, name
        assert wert not in fertig.stderr, name


def test_das_skript_traegt_keinen_umgebungs_filter() -> None:
    """Statischer Ratchet gegen die stille Variante des Fehlers.

    Der ausfuehrbare Nachweis oben prueft das Verhalten. Diese Kontrolle
    verhindert, dass jemand den Filter einzieht und den Nachweis gleich mit
    anpasst, ohne dass die Absicht im Diff sichtbar wird.
    """
    quelle = SKRIPT.read_text(encoding="utf-8")
    code = NEUZEILE.join(z for z in quelle.splitlines() if not z.lstrip().startswith("#"))

    for verboten in ("env -i", "--ignore-environment", "unset ", "export "):
        assert verboten not in code, f"das Skript greift in die Umgebung ein: {verboten!r}"
    assert 'exec "$BINARY" "$@"' in code, "der exec muss die Umgebung vollstaendig vererben"
