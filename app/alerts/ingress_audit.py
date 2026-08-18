"""Lesen des Eingangs-Audits: wann kam zuletzt ein ANGENOMMENES Event an?

Eigenes Modul, aus zwei Gründen.

**Fachlich:** ein Eingangsstrom-Audit enthält *beide* Ausgänge — angenommene und
abgewiesene Requests. Wer wissen will, ob der Eingang noch liefert, darf nicht
die Datei-Existenz oder ihre mtime messen, sondern muss den letzten Record mit
``outcome == "accepted"`` suchen. Das ist eine eigene Leseregel und gehört nicht
in die Frische-Schleife hineingeschrieben.

**Strukturell:** ``app/alerts/health_check.py`` nennt den Paper-Ausführungs-Audit
(Sekundärsignal für den Loop-Deadlock-Watchdog). Der Reader-Ratchet
(``test_paper_audit_reader_ratchet``) markiert jede ``app/``-Datei, die dessen
Marker nennt UND irgendwo ``open``/``loads`` aufruft — bewusst grob gehalten.
Der Marker wird hier deshalb NICHT ausgeschrieben: die Ratsche sucht per
Textsuche über die ganze Datei und trifft sonst diesen erklärenden Absatz
selbst (beobachtet 2026-08-18, genau ein Lauf lang).
Ein direkter Lesevorgang in ``health_check`` hätte sie ausgelöst, obwohl hier ein
ganz anderer Strom gelesen wird. Die Funktion hier hinein zu ziehen ist die
ehrliche Auflösung: die Ratsche bleibt scharf, die Baseline unangetastet, und
``health_check`` delegiert, statt selbst zu parsen.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

__all__ = ["INGRESS_TAIL_BYTES", "last_accepted_ingress_event"]

# Wieviel vom Ende der Audit-Datei gelesen wird. Die Datei ist auf dem Pi
# ~2,5 MB und der Wächter läuft alle 15 min — ein Vollscan wäre Verschwendung.
# 256 KB decken auf dem realen Stream mehrere hundert Records ab.
INGRESS_TAIL_BYTES = 256 * 1024


def last_accepted_ingress_event(path: Path) -> datetime | None:
    """Zeitpunkt des letzten ANGENOMMENEN Webhook-Events, sonst ``None``.

    Warum nicht die Datei-mtime: ein ABGELEHNTER Request schreibt ebenfalls in
    dieses Audit. Am 2026-08-18 haben drei unsignierte Diagnose-Requests
    (``outcome=rejected``, ``source_ip=127.0.0.1``) den Eingangs-Wächter sofort
    grün gefärbt — der Health-Check meldete danach „All systems healthy",
    obwohl das letzte angenommene Event vom ``2026-08-02T17:23:45Z`` stammte,
    also 16 Tage zurücklag.

    Damit kann JEDER Absender den Wächter beruhigen, auch ein Portscanner auf
    der öffentlichen Adresse. Ein Eingangs-Wächter, den Fremde stummschalten
    können, ist keiner — und der TV-Ingest-Tod war überhaupt nur deshalb sechs
    Tage unbemerkt, weil niemand auf den Eingang sah.

    Fail-soft: unlesbare oder kaputte Zeilen werden übersprungen, nicht
    eskaliert. Findet sich im gelesenen Ende kein angenommenes Event, gilt der
    Strom als nicht liefernd — das ist die konservative Richtung.
    """
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > INGRESS_TAIL_BYTES:
                handle.seek(size - INGRESS_TAIL_BYTES)
                handle.readline()  # angeschnittene erste Zeile verwerfen
            raw = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return None

    for line in reversed(raw.splitlines()):
        line = line.strip()
        if not line or '"accepted"' not in line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict) or record.get("outcome") != "accepted":
            continue
        stamp = record.get("received_at")
        if not isinstance(stamp, str):
            continue
        try:
            parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        except ValueError:
            continue
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None
