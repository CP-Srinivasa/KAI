"""Das Install-Skript muss JEDE Unit im Repo kennen, nicht die halbe.

Befund 2026-08-17 (Deploy) und live nachgezaehlt am 18.08.:

    deploy/systemd/            113 Unit-Dateien
    scripts/pi_install_systemd.sh   54 in ``UNITS`` gelistet
    -> 59 Units wuerden auf einem frischen Host NIE installiert

Darunter ``kai-prereg-maturity.timer`` (die Fristen-Uhr), ``kai-truth-anchor``
und ``kai-integrity-anchor`` (die Truth-Verankerung) sowie
``kai-backup-artifacts`` (die Sicherung der Forschungshistorie). Ein neu
aufgesetzter Pi waere zur Haelfte funktionsfaehig gewesen, ohne dass irgendetwas
das gemeldet haette — die Luecke fiel nur auf, weil 15 der 17 maskierten Units
beim 17.08.-Deploy gar nicht mitkopiert wurden und von Hand nachgezogen werden
mussten.

Zwei Listen, zwei Zwecke — und nur EINE davon darf handgepflegt sein:

* **Kopieren** (``UNITS``) ist folgenlos: eine Unit-Datei in
  ``/etc/systemd/system`` tut nichts, solange sie nicht enabled ist. Diese
  Liste wird deshalb aus dem Verzeichnis abgeleitet und kann nicht driften.
* **Scharfschalten** (``ENABLE_ON_INSTALL``) ist folgenreich und bleibt
  ausdruecklich handkuratiert (Lehre #626/#627: neue Timer nie blind
  mit-enablen).
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "scripts" / "pi_install_systemd.sh"
_UNIT_DIR = _ROOT / "deploy" / "systemd"
# Fremdbinaeries: kommen vom Hersteller, nicht aus diesem Repo. Sie gehoeren
# in die Host-Bereitstellung (docs/pi_migration), nicht in scripts/.
_VENDOR_BINARIES = frozenset({"cloudflared"})


def _script() -> str:
    return _SCRIPT.read_text(encoding="utf-8")


def _array(name: str) -> list[str]:
    match = re.search(rf"^{name}=\((.*?)^\)", _script(), re.S | re.M)
    assert match is not None, f"{name} nicht gefunden — Test veraltet?"
    return re.findall(r'"([^"]+)"', match.group(1))


def _repo_units() -> set[str]:
    return {p.name for p in _UNIT_DIR.iterdir() if p.suffix in {".service", ".timer"}}


def test_kopierliste_ist_abgeleitet_und_kann_nicht_driften() -> None:
    """``UNITS`` darf keine handgepflegte Namensliste mehr sein.

    Genau diese Liste lief 59 Units hinterher. Wird sie wieder von Hand
    gefuehrt, wiederholt sich der Befund lautlos.
    """
    src = _script()
    match = re.search(r"^UNITS=\((.*?)^\)", src, re.S | re.M)
    if match is not None:
        literals = [
            u
            for u in re.findall(r'"([^"]+)"', match.group(1))
            if u.endswith((".service", ".timer"))
        ]
        assert not literals, (
            f"UNITS traegt wieder {len(literals)} handgepflegte Unit-Namen. "
            "Die Kopierliste muss aus deploy/systemd/ abgeleitet werden."
        )
    assert "UNIT_SRC" in src and "mapfile" in src, (
        "Kein abgeleiteter Aufbau der Kopierliste gefunden."
    )


def test_scharfschaltliste_zeigt_nur_auf_existierende_units() -> None:
    """Ein Tippfehler in ENABLE_ON_INSTALL waere ein stiller No-op."""
    repo = _repo_units()
    missing = [u for u in _array("ENABLE_ON_INSTALL") if u not in repo]
    assert not missing, f"ENABLE_ON_INSTALL nennt Units ohne Datei: {missing}"


def test_reaktivierungsliste_zeigt_nur_auf_existierende_units() -> None:
    repo = _repo_units()
    missing = [u for u in _array("CRITICAL_REACTIVATE") if u not in repo]
    assert not missing, f"CRITICAL_REACTIVATE nennt Units ohne Datei: {missing}"


def test_jede_onfailure_zielunit_existiert_im_repo() -> None:
    """59x ``OnFailure=`` (#713) zeigen ins Leere, wenn das Template fehlt."""
    repo = _repo_units()
    targets: set[str] = set()
    for unit in _UNIT_DIR.iterdir():
        if unit.suffix not in {".service", ".timer"}:
            continue
        for line in unit.read_text(encoding="utf-8").splitlines():
            if line.startswith("OnFailure="):
                for target in line.split("=", 1)[1].split():
                    # Template-Instanz kai-foo@arg.service -> Datei kai-foo@.service
                    targets.add(re.sub(r"@[^.]*\.", "@.", target))
    missing = sorted(t for t in targets if t not in repo)
    assert not missing, f"OnFailure= zeigt auf fehlende Units: {missing}"


def test_absolute_execstart_ziele_werden_mitinstalliert() -> None:
    """Eine Unit, deren ExecStart auf einen Pfad ausserhalb des Checkouts zeigt,
    braucht einen Installationsschritt fuer genau diese Datei.

    ``kai-standby-{data,system}.service`` zeigen auf
    ``/usr/local/bin/standby_to_usb.sh``. Bis 2026-08-18 lagen Unit UND Skript
    nur auf dem Pi; jetzt liegen beide im Repo — und das Skript muss auch
    ausgerollt werden, sonst installiert ein frischer Host eine Sicherung, die
    sofort fehlschlaegt.
    """
    src = _script()
    external: set[str] = set()
    for unit in _UNIT_DIR.glob("*.service"):
        for line in unit.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line.startswith("ExecStart="):
                continue
            target = line.split("=", 1)[1].lstrip("-+!@").split()[0]
            if target.startswith("/usr/local/"):
                external.add(target)
    for target in sorted(external):
        if Path(target).name in _VENDOR_BINARIES:
            continue
        repo_copy = _ROOT / "scripts" / Path(target).name
        assert repo_copy.exists(), (
            f"{target} ist ExecStart-Ziel einer Unit, hat aber keine kanonische "
            "Quelle unter scripts/ — es existiert dann nur auf dem laufenden Host."
        )
        assert target in src, (
            f"{target} ist ExecStart-Ziel einer Unit, wird vom Install-Skript "
            "aber nicht ausgerollt."
        )
