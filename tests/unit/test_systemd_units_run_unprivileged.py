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
ROOT_ALLOWED: frozenset[str] = frozenset()


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
    assert "sudo -n systemctl start" in text
    # Lesende Aufrufe duerfen NIE ueber sudo laufen.
    assert "sudo -n systemctl is-active" not in text
    assert "sudo -n systemctl list-unit-files" not in text


def test_sudoers_vorlage_ist_eng_gefasst() -> None:
    tpl = UNIT_DIR.parent / "sudoers.d" / "kai-service-watchdog"
    text = tpl.read_text(encoding="utf-8")

    assert "systemctl start kai-*" in text
    # Keine Blankovollmacht in der Vorlage.
    assert "NOPASSWD: ALL" not in text
