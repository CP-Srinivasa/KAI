"""Keine KAI-Unit darf unnötig als root laufen.

``kai-service-watchdog.service`` hatte kein ``User=`` und lief damit als root —
während es ein Skript ausführte, das dem unprivilegierten Service-User gehört
und von ihm beschreibbar ist (`-rw-rw-r-- ubuntu ubuntu`, verifiziert auf dem Pi
am 09.08.). Alle übrigen 57 Units laufen als ``ubuntu``. Wer diesen User
kompromittiert, hätte den Skriptinhalt getauscht und beim nächsten Tick root
ausgeführt.

Dieser Test ist ein Ratchet: neue Units müssen ``User=`` setzen, oder die
Ausnahme hier bewusst und begründet eintragen.
"""

from __future__ import annotations

from pathlib import Path

import pytest

UNIT_DIR = Path(__file__).resolve().parents[2] / "deploy" / "systemd"

# Units, die begründet ohne User= laufen dürfen. Leer = keine.
# Eintragen heißt: "läuft als root, und das ist hier nötig" — mit Begründung.
# Begruendete root-Ausnahmen. Jeder Eintrag braucht einen Grund, der aus dem
# ExecStart nachpruefbar ist — sonst ist die Liste nur ein Ventil.
#
# Die beiden Cold-Standby-Tier laufen als root, weil sie genau das sichern, was
# ``ubuntu`` nicht lesen kann: ``/etc/systemd/system`` + ``/etc/fstab`` (Tier
# "system") und den root-eigenen exfat-Mount ``/mnt/kai-data`` als Ziel (beide
# Tier). ``User=ubuntu`` wuerde die Sicherung still unvollstaendig machen —
# schlimmer als der root-Lauf, denn ein unbrauchbares Backup faellt erst beim
# Restore auf. Beide sind ``Type=oneshot``, schreiben ausschliesslich unter
# ``/mnt/kai-data/kai-standby/`` und loeschen nur eigene Archive (Retention).
ROOT_ALLOWED: frozenset[str] = frozenset(
    {
        "kai-standby-data.service",
        "kai-standby-system.service",
    }
)


def _service_files() -> list[Path]:
    return sorted(UNIT_DIR.glob("*.service"))


def _directives(path: Path) -> list[str]:
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def test_es_gibt_ueberhaupt_units() -> None:
    assert _service_files(), "Keine Unit-Dateien gefunden — Pfad falsch?"


@pytest.mark.parametrize("unit", _service_files(), ids=lambda p: p.name)
def test_unit_setzt_einen_user(unit: Path) -> None:
    if unit.name in ROOT_ALLOWED:
        pytest.skip(f"{unit.name} ist als root-Ausnahme eingetragen")

    directives = _directives(unit)
    has_user = any(line.startswith("User=") for line in directives)

    assert has_user, (
        f"{unit.name} setzt kein User= und laeuft damit als root. "
        "Setze User=ubuntu, oder trage die Unit begruendet in ROOT_ALLOWED ein."
    )


def test_watchdog_laeuft_als_ubuntu() -> None:
    """Der konkrete Eskalationspfad aus dem Audit."""
    unit = UNIT_DIR / "kai-service-watchdog.service"
    directives = _directives(unit)

    assert "User=ubuntu" in directives
    # sudo braucht NoNewPrivileges=false — sonst wird das setuid-Binary
    # blockiert und der Watchdog kann keinen Dienst mehr starten.
    assert "NoNewPrivileges=false" in directives


def test_watchdog_skript_hebt_sich_nur_punktuell() -> None:
    """Nur der mutierende Aufruf nutzt sudo; Abfragen bleiben unprivilegiert."""
    script = UNIT_DIR.parents[0].parent / "scripts" / "pi_service_watchdog.sh"
    text = script.read_text(encoding="utf-8")

    assert "systemctl_start()" in text
    assert "sudo -n /usr/local/sbin/kai-service-control start" in text
    # Lesende Aufrufe duerfen NIE ueber sudo laufen.
    assert "sudo -n systemctl is-active" not in text
    assert "sudo -n systemctl list-unit-files" not in text


# Praefixe, unter denen nur root schreiben kann. Ein ExecStart darunter kann von
# `ubuntu` nicht ausgetauscht werden; alles unter /home/ oder im Arbeitsbaum kann es.
_ROOT_OWNED_PREFIXES = (
    "/usr/local/bin/",
    "/usr/local/sbin/",
    "/usr/bin/",
    "/usr/sbin/",
    "/bin/",
    "/sbin/",
)


# Interpreter: hier ist das ERSTE Token harmlos (/usr/bin/bash), entscheidend ist
# das zweite — das Skript, das er ausfuehrt.
_INTERPRETERS = frozenset({"bash", "sh", "dash", "zsh", "python", "python3", "perl", "ruby", "env"})

# Direktiven, die als root Code ausfuehren oder Code-Herkunft bestimmen.
_EXEC_DIRECTIVES = (
    "ExecStart=",
    "ExecStartPre=",
    "ExecStartPost=",
    "ExecReload=",
    "ExecStop=",
    "ExecStopPost=",
    "ExecCondition=",
)


def _executed_paths(unit: Path) -> list[tuple[str, str]]:
    """(Direktive, ausgefuehrter Pfad) — inkl. des Skripts hinter einem Interpreter."""
    out: list[tuple[str, str]] = []
    for line in _directives(unit):
        if not line.startswith(_EXEC_DIRECTIVES):
            continue
        key, _, value = line.partition("=")
        tokens = value.lstrip("-@+!:").split()
        if not tokens:
            continue
        out.append((key, tokens[0]))
        # Wrapper: `/usr/bin/bash /home/ubuntu/x.sh` besteht den reinen
        # Prefix-Test, fuehrt als root aber ein beschreibbares Skript aus.
        if Path(tokens[0]).name in _INTERPRETERS:
            for tok in tokens[1:]:
                if tok.startswith("-"):
                    continue
                out.append((f"{key} (via {Path(tokens[0]).name})", tok))
                break
    return out


@pytest.mark.parametrize("name", sorted(ROOT_ALLOWED))
def test_root_ausnahme_fuehrt_nur_root_eigenen_code_aus(name: str) -> None:
    """Eine root-Unit darf keinen Code starten, den ``ubuntu`` austauschen kann.

    Geprueft wird JEDE Exec*-Direktive (nicht nur ExecStart) und bei einem
    Interpreter-Aufruf zusaetzlich das Skript dahinter — `/usr/bin/bash
    /home/ubuntu/mutable.sh` wuerde den reinen Prefix-Test sonst bestehen,
    obwohl root anschliessend ein beschreibbares Skript ausfuehrt.

    Seit dem Broker (2026-08-19) ist das nicht mehr die einzige Verteidigung:
    `kai-service-control` weist Units ohne ``User=ubuntu`` grundsaetzlich ab, sie
    sind also gar nicht passwortfrei steuerbar. Dieser Test bleibt die zweite
    Linie fuer alles, was Root-Units sonst noch anstossen kann (Timer, Boot).
    """
    unit = UNIT_DIR / name
    executed = _executed_paths(unit)
    assert executed, f"{name} hat keine Exec*-Direktive"

    for directive, target in executed:
        assert target.startswith(_ROOT_OWNED_PREFIXES), (
            f"{name}: {directive} fuehrt {target!r} aus — nicht unter einem "
            "root-eigenen Praefix. Eine root-Unit mit von `ubuntu` beschreibbarem "
            "Code macht jede NOPASSWD-Regel darauf wertlos."
        )
        assert not target.startswith("/home/"), f"{name}: {directive} unter /home/ ist beschreibbar"


@pytest.mark.parametrize("name", sorted(ROOT_ALLOWED))
def test_root_ausnahme_zieht_keine_beschreibbare_umgebung(name: str) -> None:
    """EnvironmentFile/WorkingDirectory einer root-Unit duerfen nicht ubuntu gehoeren."""
    unit = UNIT_DIR / name
    for line in _directives(unit):
        for key in ("EnvironmentFile=", "WorkingDirectory="):
            if not line.startswith(key):
                continue
            value = line.partition("=")[2].lstrip("-").strip()
            assert not value.startswith("/home/"), (
                f"{name}: {key}{value} liegt unter /home/ und ist damit fuer "
                "`ubuntu` beschreibbar — eine root-Unit darf das nicht laden."
            )


def test_deploy_sudoers_vorlage_hat_kein_argument_glob() -> None:
    """P0 2026-08-19: ein Argument-Glob in sudoers ist umgehbar.

    sudoers matcht Argumente als EINEN zusammenhaengenden String, und `*` matcht
    auch Leerzeichen — `systemctl restart kai-x.service ssh.service` wurde von der
    Vorgaengerregel autorisiert (live verifiziert). Die Vorlage darf deshalb nur
    noch den Broker-Pfad nennen; was zulaessig ist, entscheidet das Skript.
    """
    tpl = UNIT_DIR.parent / "sudoers.d" / "kai-deploy"
    effective = "\n".join(
        line
        for line in tpl.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )

    assert "/usr/local/sbin/kai-service-control" in effective
    assert "NOPASSWD: KAI_SERVICE_BROKER" in effective
    # Kein systemctl und kein Glob in einer wirksamen Zeile.
    assert "systemctl" not in effective, "sudoers darf systemctl nicht mehr direkt freigeben"
    assert "*" not in effective, "Argument-Wildcards in sudoers sind umgehbar"
    for verboten in ("apt-get", "pi_install_systemd.sh", "bash", "visudo", "install"):
        assert verboten not in effective, f"{verboten} gehoert nicht in die Allowlist"


def test_broker_ist_der_einzige_passwortfreie_pfad() -> None:
    """Nur EINE Vorlage, und die zeigt auf den Broker."""
    tpl_dir = UNIT_DIR.parent / "sudoers.d"
    vorlagen = sorted(p.name for p in tpl_dir.iterdir() if p.is_file())
    assert vorlagen == ["kai-deploy"], (
        f"unerwartete sudoers-Vorlagen: {vorlagen}. Die alte Glob-Regel "
        "kai-service-watchdog trug denselben Bypass und wurde entfernt."
    )


def test_watchdog_ruft_den_broker_nicht_systemctl() -> None:
    script = UNIT_DIR.parents[0].parent / "scripts" / "pi_service_watchdog.sh"
    text = script.read_text(encoding="utf-8")
    wirksam = "\n".join(
        ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")
    )
    assert "sudo -n /usr/local/sbin/kai-service-control start" in wirksam
    assert "sudo -n systemctl" not in wirksam
