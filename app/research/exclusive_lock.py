"""Ein Abschnitt, den genau einer betritt — fuer alle Schreiber der Wahrheitsschicht.

Herausgeloest aus ``frozen_input``, wo er fuer das Publizieren des eingefrorenen
Artefakts entstand (#767). Die Checkpoint- und Verdikt-Journale haben dieselbe
Race: beide lesen erst das Journal, pruefen, und haengen dann an. Zwei Prozesse
koennen beide "kein Eintrag vorhanden" sehen und beide schreiben — beim Verdikt
entstuenden zwei autoritative Zeilen, beim Checkpoint zwei konkurrierende
Entscheidungen.

Zwei Implementierungen desselben Locks waeren genau die Doppelung, die spaeter
auseinanderlaeuft: haerte man eine, kassiert die andere die Reparatur.

``O_CREAT | O_EXCL`` ist die eine Operation, die das Dateisystem selbst
serialisiert — genau einer gewinnt, alle anderen sehen ``FileExistsError``. Ein
Lock, der erst NACH dem Lesen genommen wuerde, schloesse die Race nicht; deshalb
gehoert alles hinein: lesen, pruefen, schreiben.

EHRLICHE GRENZE: ein Prozess, der mitten im Abschnitt stirbt, laesst die
Lock-Datei liegen; nachfolgende Schreiber laufen in den Timeout und brechen ab.
Das ist die gewollte Richtung — ein blockierter Schreibvorgang ist harmlos, ein
doppelter nicht. Aufgeraeumt wird von Hand, sichtbar.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

DEFAULT_LOCK_TIMEOUT_S = 30.0


class ExclusiveLockError(RuntimeError):
    """Der Abschnitt ist belegt — fail-closed, kein zweiter Schreiber."""


@contextmanager
def exclusive_lock(
    lock_path: Path,
    *,
    timeout_s: float = DEFAULT_LOCK_TIMEOUT_S,
    what: str = "Schreibvorgang",
) -> Iterator[None]:
    """Exklusiv ueber ``O_CREAT | O_EXCL``.

    Args:
        lock_path: die Lock-Datei. Ihr Verzeichnis muss existieren.
        timeout_s: wie lange auf einen belegten Lock gewartet wird.
        what: benennt den Vorgang in der Fehlermeldung.

    Raises:
        ExclusiveLockError: der Lock war ueber ``timeout_s`` belegt.
    """
    deadline = time.monotonic() + timeout_s
    last_error: OSError | None = None
    while True:
        try:
            handle = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            break
        except (FileExistsError, PermissionError) as exc:
            # ``PermissionError`` gehoert dazu: gibt ein Schreiber den Lock
            # gerade frei, meldet Windows fuer die noch nicht abgeschlossene
            # Loeschung "Permission denied" statt "File exists". Wer nur
            # ``FileExistsError`` faengt, laesst einen harmlosen Wiederanlauf
            # scheitern. Eine echte Rechteverweigerung laeuft dagegen in den
            # Timeout und traegt die Ursache als ``__cause__``.
            last_error = exc
            if time.monotonic() >= deadline:
                raise ExclusiveLockError(
                    f"{lock_path} ist seit ueber {timeout_s:.0f}s belegt — ein anderer "
                    f"{what} laeuft oder ist abgestuerzt. Kein zweiter."
                ) from last_error
            time.sleep(0.005)
    try:
        os.write(handle, f"{os.getpid()}\n".encode())
    finally:
        os.close(handle)
    try:
        yield
    finally:
        lock_path.unlink(missing_ok=True)
