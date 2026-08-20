r"""Eine privilegierte Policy darf nie ohne ihr privilegiertes Ziel ausgerollt werden.

Vorfall 2026-08-20, live auf dem Pi gemessen:

    sudo -n -l  ->  (root) NOPASSWD: /usr/local/sbin/kai-service-control
    ls /usr/local/sbin/kai-service-control  ->  No such file or directory

Die sudoers-Regel war ausgerollt, das Ziel nicht installiert. Folge: JEDER
passwortfreie privilegierte Pfad war tot — nicht nur der Unit-Sync, sondern auch
die Auto-Recovery des Service-Watchdogs, der in
``scripts/pi_service_watchdog.sh`` genau diesen Broker aufruft.

Die Sicherheit ist dabei in die richtige Richtung gescheitert (fail-closed), aber
KAI dokumentierte damit wochenlang eine Recovery-Faehigkeit, die es live nicht
gab. #734 hatte die Reihenfolge "erst Broker, dann sudoers" sogar ausdruecklich
als zwingend beschrieben — die Ausfuehrung geriet in genau den Zustand, den die
PR selbst als unzulaessig benannte.

Daraus die Invariante, die dieser Test haelt:

    sudoers verweist auf einen Pfad
        -> die Datei MUSS existieren
        -> Eigentuemer root, Gruppe root, Mode 0755
        -> Inhalt MUSS dem freigegebenen Artefakt entsprechen

Der zweite Contract in dieser Datei schliesst die Luecke, die beim Review von
#734 sichtbar wurde: Was der Watchdog vom Broker VERLANGT, muss der Broker auch
koennen — sonst ist die Recovery nur auf dem Papier vorhanden.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_BROKER_SRC = _ROOT / "deploy" / "bin" / "kai-service-control"
_SUDOERS_DIR = _ROOT / "deploy" / "sudoers.d"
_INSTALLER = _ROOT / "scripts" / "pi_install_systemd.sh"
_WATCHDOG = _ROOT / "scripts" / "pi_service_watchdog.sh"

_BROKER_DST = "/usr/local/sbin/kai-service-control"


def _sudoers_text() -> str:
    assert _SUDOERS_DIR.is_dir(), "deploy/sudoers.d fehlt"
    return "\n".join(f.read_text(encoding="utf-8") for f in sorted(_SUDOERS_DIR.iterdir()))


# ── Contract 1: Policy ohne Ziel darf es nicht geben ────────────────────────


def test_broker_artifact_exists_in_repo() -> None:
    """Die sudoers-Regel zeigt auf ein Artefakt — das muss im Repo liegen."""
    assert _BROKER_SRC.is_file(), (
        f"{_BROKER_SRC} fehlt, aber sudoers erlaubt {_BROKER_DST} passwortfrei"
    )


def test_every_nopasswd_target_is_installed_by_the_installer() -> None:
    """Jeder NOPASSWD-Pfad braucht einen reproduzierbaren Installationsschritt.

    Genau das fehlte: ``pi_install_systemd.sh`` installierte den Standby-Helper,
    aber nicht den Broker. Ein frischer Host haette denselben toten
    Recovery-Pfad bekommen.
    """
    text = _sudoers_text()
    # Die Policy verweist ueber einen Cmnd_Alias, nicht ueber einen rohen Pfad —
    # beides muss aufgeloest werden, sonst prueft der Test ins Leere.
    aliases = dict(re.findall(r"^\s*Cmnd_Alias\s+(\w+)\s*=\s*(.+)$", text, re.MULTILINE))
    targets: set[str] = set()
    for raw in re.findall(r"NOPASSWD:\s*(.+)$", text, re.MULTILINE):
        for token in (t.strip() for t in raw.split(",")):
            if token.startswith("/"):
                targets.add(token.split()[0])
            elif token in aliases:
                targets.update(
                    part.strip().split()[0]
                    for part in aliases[token].split(",")
                    if part.strip().startswith("/")
                )
    assert targets, "keine NOPASSWD-Ziele aufloesbar — Policy-Datei kaputt?"

    installer = _INSTALLER.read_text(encoding="utf-8")
    missing = [t for t in sorted(targets) if t not in installer]

    assert not missing, (
        "Diese passwortfrei erlaubten Ziele werden von keinem Installationsschritt "
        "ausgerollt:\n  " + "\n  ".join(missing) + "\n"
        "Eine Policy ohne installiertes Ziel ist keine Sicherheit, sondern ein "
        "toter Pfad — 2026-08-20 war genau so die Watchdog-Recovery lautlos weg."
    )


def test_installer_places_broker_as_root_owned_and_executable() -> None:
    """root:root 0755 — nicht ``ubuntu``-schreibbar.

    Ein von ``ubuntu`` beschreibbares Ziel mit NOPASSWD waere exakt so viel wert
    wie ``NOPASSWD:ALL``: der Inhalt ist das Privileg, nicht der Dateiname.
    """
    installer = _INSTALLER.read_text(encoding="utf-8")
    assert _BROKER_DST in installer, "Broker-Ziel fehlt im Installer"
    window = installer[max(0, installer.index(_BROKER_DST) - 800) :]
    assert "0755" in window, "Broker muss mit Mode 0755 installiert werden"
    assert "root" in window, "Broker muss root:root gehoeren"


def test_installer_puts_the_broker_before_the_sudoers_policy() -> None:
    """Reihenfolge ist die Invariante, nicht nur ein Detail.

    Erst das Ziel, dann die Regel — sonst existiert zwischenzeitlich eine Policy
    ohne Ziel, und genau dieser Zwischenzustand wurde am 2026-08-20 dauerhaft.
    """
    installer = _INSTALLER.read_text(encoding="utf-8")
    if "sudoers.d" not in installer:
        return  # Installer rollt die Policy nicht aus — dann gibt es keine Reihenfolge
    assert installer.index(_BROKER_DST) < installer.index("sudoers.d"), (
        "Broker muss VOR der sudoers-Policy installiert werden"
    )


# ── Contract 2: Was der Watchdog verlangt, muss der Broker koennen ──────────


def _broker_text() -> str:
    return _BROKER_SRC.read_text(encoding="utf-8")


def test_broker_accepts_only_service_units_and_watchdog_knows_it() -> None:
    """Der Broker verankert ``^kai-...\\.service$`` — Timer kann er NICHT.

    Der Watchdog reconciled ``kai-*.timer`` ueber denselben Broker-Aufruf. Ohne
    diesen Contract bleibt die Timer-Recovery auch bei korrekt installiertem
    Broker wirkungslos — sie wuerde bei jedem Versuch abgewiesen.

    Aufgeloest wird das NICHT, indem der Broker Timer akzeptiert (das waere ein
    weiterer Root-Pfad fuer eine Unit-Klasse, die niemand geprueft hat), sondern
    indem der Watchdog Timer als **alert-only** deklariert. Erkennen kann sie
    seit #738 der Scheduleability-Waechter.
    """
    assert re.search(r"kai-\[A-Za-z0-9_\.-\]\+.*\\\.service\$", _broker_text()), (
        "Broker-Unitmuster nicht gefunden — Contract kann nicht geprueft werden"
    )
    watchdog = _WATCHDOG.read_text(encoding="utf-8")
    assert "TIMER_RECONCILE_ALERT_ONLY" in watchdog, (
        "Der Watchdog muss ausdruecklich deklarieren, dass Timer-Reconcile nur "
        "alarmiert und nicht ueber den Broker zu reparieren versucht wird"
    )


def test_watchdog_units_are_either_broker_safe_or_declared_alert_only() -> None:
    """``cloudflared`` ist per Default in der Watchdog-Liste, aber nicht broker-faehig.

    Der Broker verlangt das Praefix ``kai-`` und ein nichtleeres ``User=``.
    ``cloudflared`` erfuellt das erste nicht — und traegt zusaetzlich ein
    ``ExecStartPre=+``, das mit erhoehten Rechten laeuft. ``User=ubuntu`` allein
    heisst also nicht "sicher passwortfrei startbar". Deshalb: nur ueberwachen.
    """
    watchdog = _WATCHDOG.read_text(encoding="utf-8")
    match = re.search(r'^UNITS_DEFAULT="([^"]+)"', watchdog, re.MULTILINE)
    assert match, "UNITS_DEFAULT nicht gefunden"

    raw_alert_only = re.search(r'^ALERT_ONLY_UNITS="([^"]*)"', watchdog, re.MULTILINE)
    alert_only: set[str] = set()
    if raw_alert_only:
        value = raw_alert_only.group(1)
        # Shell-Default-Syntax aufloesen: ${VAR:-default} -> default
        default = re.search(r"\$\{[A-Za-z_][A-Za-z0-9_]*:-([^}]*)\}", value)
        alert_only = set((default.group(1) if default else value).split())

    offenders = [
        unit
        for unit in match.group(1).split()
        if not unit.startswith("kai-") and unit not in alert_only
    ]

    assert not offenders, (
        "Diese ueberwachten Units kann der Broker nicht starten und sie sind nicht "
        "als alert-only deklariert:\n  " + "\n  ".join(offenders) + "\n"
        "Entweder broker-faehig machen (geprueft!) oder ausdruecklich alert-only."
    )


# ── Laufzeit: der Zustand, der am 2026-08-20 wochenlang unbemerkt blieb ─────


def test_missing_broker_is_a_finding() -> None:
    """Der Realfall: Policy vorhanden, Ziel nicht."""
    from app.services.timer_health import BrokerState, evaluate_privilege_broker

    finding = evaluate_privilege_broker(BrokerState(path=_BROKER_DST, exists=False))

    assert finding is not None
    assert "FEHLT" in finding
    assert "Watchdog" in finding, "die Folge muss im Befund stehen, nicht nur der Zustand"


def test_user_writable_broker_is_a_finding() -> None:
    """Ein fuer ``ubuntu`` schreibbares NOPASSWD-Ziel ist NOPASSWD:ALL in gruen."""
    from app.services.timer_health import BrokerState, evaluate_privilege_broker

    finding = evaluate_privilege_broker(
        BrokerState(
            path=_BROKER_DST,
            exists=True,
            owner="ubuntu",
            group="ubuntu",
            mode="664",
            matches_repo_artifact=True,
        )
    )

    assert finding is not None
    assert "root:root" in finding


def test_content_drift_is_a_finding() -> None:
    """Installiert darf nicht anderer Code laufen als der gepruefte."""
    from app.services.timer_health import BrokerState, evaluate_privilege_broker

    finding = evaluate_privilege_broker(
        BrokerState(
            path=_BROKER_DST,
            exists=True,
            owner="root",
            group="root",
            mode="755",
            matches_repo_artifact=False,
        )
    )

    assert finding is not None
    assert "weicht vom Repo-Artefakt ab" in finding


def test_correctly_installed_broker_is_silent() -> None:
    """Gegenprobe: der gesunde Zustand darf keinen Daueralarm erzeugen."""
    from app.services.timer_health import BrokerState, evaluate_privilege_broker

    assert (
        evaluate_privilege_broker(
            BrokerState(
                path=_BROKER_DST,
                exists=True,
                owner="root",
                group="root",
                mode="755",
                matches_repo_artifact=True,
            )
        )
        is None
    )
