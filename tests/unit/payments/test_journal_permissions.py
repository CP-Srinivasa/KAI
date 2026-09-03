"""Das Geld-Journal gehoert dem Dienstnutzer allein (ADR 0018 §5/§11).

``0600`` greift nur beim ERSTEN Anlegen der Datei — danach fasst der Writer
den Modus nicht mehr an. Genau deshalb ist das die Stelle, an der die Zusage
still verlorengehen kann: wer den Aufruf entfernt, merkt es nie, weil eine
bestehende Datei ihren Modus behaelt.

Der Modus selbst ist POSIX; auf Windows ist ``harden_permissions`` ein
bewusstes No-op (der Pi ist die Umgebung, fuer die die Zusage gilt). Der
Verdrahtungstest laeuft trotzdem ueberall: er prueft, DASS die Haertung fuer
die neue Datei gerufen wird — die Plattformfrage beantwortet dann
``journal_fs``.
"""

from __future__ import annotations

import os
import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.payments import journal as journal_module
from app.payments.journal import PaymentJournal
from app.payments.journal_fs import OWNER_ONLY

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)


def test_die_haertung_wird_fuer_die_neue_datei_gerufen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hardened: list[Path] = []
    monkeypatch.setattr(journal_module, "harden_permissions", hardened.append)

    journal = PaymentJournal(tmp_path / "payments" / "payment_journal.jsonl")
    journal.open()
    journal.append("pi_1", "intent_created", {"status": "REQUESTED"}, ts=NOW)

    assert hardened == [journal.path]


def test_die_haertung_wird_nicht_bei_jedem_append_wiederholt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ein ``chmod`` je Record waere ein Syscall pro Zahlung ohne Zusatznutzen —
    und ueberschriebe eine bewusste Operator-Aenderung stillschweigend."""
    hardened: list[Path] = []
    monkeypatch.setattr(journal_module, "harden_permissions", hardened.append)

    journal = PaymentJournal(tmp_path / "payments" / "payment_journal.jsonl")
    journal.open()
    for index in range(3):
        journal.append(f"pi_{index}", "intent_created", {"status": "REQUESTED"}, ts=NOW)

    assert len(hardened) == 1


@pytest.mark.skipif(os.name != "posix", reason="Dateimodi gibt es nur auf POSIX")
def test_das_journal_liegt_mit_0600_auf_der_platte(tmp_path: Path) -> None:
    journal = PaymentJournal(tmp_path / "payments" / "payment_journal.jsonl")
    journal.open()
    journal.append("pi_1", "intent_created", {"status": "REQUESTED"}, ts=NOW)

    mode = stat.S_IMODE(journal.path.stat().st_mode)
    assert mode == OWNER_ONLY, f"erwartet 0600, gefunden {mode:04o}"
