"""Dateisystem-Haertung fuer Geld-Artefakte (ADR 0017 §5/§11).

Zwei Massnahmen, die beide nur beim ERSTEN Anlegen einer Datei greifen und
deshalb leicht vergessen werden:

* **Verzeichnis-fsync.** Ein ``fsync`` auf das Dateihandle sichert den INHALT.
  Der Verzeichniseintrag kann trotzdem noch im Page-Cache stehen — ein
  Stromausfall danach laesst die Datei komplett verschwinden. Beim ersten
  Record des Geld-Journals waere das der Verlust der gesamten Wahrheit ueber
  eine Zahlung, die vielleicht schon draussen ist.
* **0600.** Journal und HOTP-Material gehoeren dem Dienstnutzer allein.

Beides ist POSIX-only und auf Windows ein bewusstes No-op: Entwicklungsrechner
sind nicht die Umgebung, fuer die diese Zusagen gelten (der Pi ist es).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

#: Nur der Eigentuemer darf lesen und schreiben.
OWNER_ONLY = 0o600


def fsync_directory(directory: Path) -> None:
    """fsync des Verzeichniseintrags (Muster ``ops_ledger._fsync_directory``)."""
    if os.name != "posix":
        return
    flags = os.O_RDONLY | int(getattr(os, "O_DIRECTORY", 0))
    try:
        fd = os.open(directory, flags)
    except OSError as exc:
        logger.warning("payment_journal_dir_fsync_skipped", extra={"error": str(exc)})
        return
    try:
        os.fsync(fd)
    except OSError as exc:  # pragma: no cover - best effort
        logger.warning("payment_journal_dir_fsync_failed", extra={"error": str(exc)})
    finally:
        os.close(fd)


def harden_permissions(path: Path) -> None:
    """Setze ``0600`` — best effort, aber nicht still.

    Ein fehlgeschlagenes ``chmod`` darf den Geldpfad nicht anhalten (die Datei
    ist dann bereits geschrieben), aber es darf auch nicht unbemerkt bleiben.
    """
    if os.name != "posix":
        return
    try:
        os.chmod(path, OWNER_ONLY)
    except OSError as exc:  # pragma: no cover - best effort
        logger.warning("payment_journal_chmod_failed", extra={"error": str(exc)})


__all__ = ["OWNER_ONLY", "fsync_directory", "harden_permissions"]
