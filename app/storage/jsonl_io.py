"""Shared JSONL reader with retry-on-truncate (NEO-P-002 extended).

D-194 / NEO-F-META-20260424-029. The original retry-on-truncate policy
(NEO-P-002 D / D-156h) was inlined only in :mod:`app.alerts.audit` as
``_read_jsonl_tolerant``. Other JSONL reader call sites — in
:mod:`app.agents.worker`, :mod:`app.api.routers.agents`, and
:mod:`app.execution.envelope_to_paper_bridge` — silently drop a partial
last line when the reader races with a writer mid-append.

Under normal 10-minute-cron + polling-API load on the laptop we never
observed the race in practice, but:

* the cron frequency rises after Pi-migration (systemd timer + possibly
  several per-minute reads from the dashboard polling hook), and
* append-only writes on Windows do not guarantee POSIX append-atomicity,

so the defensive single-retry policy should apply to every reader of
append-only JSONL files. This module centralises the policy so a future
flip to e.g. ``filelock`` does not require touching each call site.

Public API:

* :func:`read_jsonl_tolerant` — the canonical entry point (full read, with
  the single retry-on-truncate policy; use when the latest line matters).
* :func:`iter_jsonl_tolerant` — constant-memory streaming variant for
  aggregation-only read paths (count/sum/tail) on large append-only files.
* :func:`RETRY_SLEEP_SECONDS` — policy constant kept as module attribute
  so tests can monkey-patch it without touching import-order edge cases.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

# 100 ms retry delay — chosen in NEO-P-002 D after empirical observation
# that a writer's fsync-plus-append takes < 50 ms on SSD; 100 ms keeps the
# latency impact on readers well below the 1 s cron-tick budget.
RETRY_SLEEP_SECONDS: float = 0.1


def read_jsonl_tolerant(
    path: Path,
    *,
    tail: int | None = None,
    dict_only: bool = True,
) -> list[dict[str, Any]]:
    """Read JSON objects from a JSONL file with one retry on partial tail.

    Policy
    ------
    * Missing file → empty list (callers always treat that as "no rows yet").
    * Mid-file :class:`json.JSONDecodeError` → silently skipped (legacy
      behaviour; mid-file corruption is rare with append-only writes).
    * Last non-empty line fails to decode → sleep
      ``RETRY_SLEEP_SECONDS`` and re-read the whole file once. On the
      second failure the line is dropped. Closes the reader-vs-writer race
      identified in NEO-P-002 D (D-156h).

    Parameters
    ----------
    path:
        File to read. The caller is responsible for resolving relative
        paths against the project root.
    tail:
        If set, return only the last *N* rows. Implemented after the
        parse to keep mid-file skip-semantics stable regardless of
        ``tail``.
    dict_only:
        When ``True`` (default), non-dict JSON values (arrays, strings,
        ``null``) are dropped. This preserves the legacy semantics of the
        three call sites migrated in D-194. Set to ``False`` only when a
        JSONL file is known to contain non-object records.

    Returns
    -------
    list[dict[str, Any]]
        Parsed records in file order.
    """

    if not path.exists():
        return []

    def _parse(text: str) -> tuple[list[dict[str, Any]], bool]:
        lines = [ln for ln in text.splitlines() if ln.strip()]
        if not lines:
            return [], False
        records: list[dict[str, Any]] = []
        last_idx = len(lines) - 1
        last_failed = False
        for idx, line in enumerate(lines):
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                if idx == last_idx:
                    last_failed = True
                continue
            if dict_only and not isinstance(obj, dict):
                continue
            records.append(obj)
        return records, last_failed

    records, last_failed = _parse(path.read_text(encoding="utf-8"))
    if last_failed:
        time.sleep(RETRY_SLEEP_SECONDS)
        records, _ = _parse(path.read_text(encoding="utf-8"))

    if tail is None:
        return records
    if tail <= 0:
        # ``tail=0`` means "last zero rows" — explicit empty slice,
        # matching caller expectations. ``records[-0:]`` would yield the
        # whole list because of Python's ``-0 == 0``.
        return []
    return records[-tail:]


def iter_jsonl_tolerant(
    path: Path,
    *,
    dict_only: bool = True,
) -> Iterator[dict[str, Any]]:
    """Stream JSON objects from a JSONL file with constant memory.

    Companion to :func:`read_jsonl_tolerant` for read paths that only need to
    *aggregate* (count, sum, tail) and must not hold the whole file in RAM.
    The dashboard polls multi-MB append-only audit files every few seconds; the
    legacy ``path.read_text().splitlines()`` pattern peaks at hundreds of MB on
    the Raspberry Pi for the ~27 MB ``trading_loop_audit.jsonl`` and is the
    direct OOM risk this function exists to remove (KAI-01).

    Policy (matches the non-retry parts of :func:`read_jsonl_tolerant`):

    * Missing file → empty iterator.
    * Any line that fails to JSON-decode is skipped — mid-file corruption and a
      racing partial final line alike. Unlike :func:`read_jsonl_tolerant` there
      is **no** sleep-and-reread retry: a partial final line from a concurrent
      appender is simply skipped this pass and picked up on the next read. This
      is safe for the repeated-poll aggregation callers and avoids both the full
      re-read and any duplicate-yield risk. State-critical readers that must not
      miss the latest line should keep using :func:`read_jsonl_tolerant`.
    * When ``dict_only`` (default) non-dict JSON values are skipped, matching
      the migrated call sites' legacy semantics.
    """

    if not path.exists():
        return
    with path.open(encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if dict_only and not isinstance(obj, dict):
                continue
            yield obj


# Rückwärts-Leseblock. 256 KiB deckt bei den Audit-Streams deutlich mehr als ein
# 24-h-Fenster ab (1.203 Zeilen ≈ 750 KiB bei trading_loop_audit), sodass in der
# Praxis 1–4 Chunks genügen; der Reader erweitert bei Bedarf trotzdem weiter.
_WINDOW_CHUNK_BYTES = 256 * 1024

# Toleranz gegen LOKALE Unordnung. Die Streams sind append-only, aber NICHT streng
# monoton: parallele Appends erzeugen kleine Rücksprünge. Auf dem Live-File
# gemessen (2026-07-30, trading_loop_audit.jsonl, 88.321 Zeilen): 143
# Sortierverletzungen (0,16 %), maximale Positions-Verschiebung **7 Zeilen**,
# maximaler Zeit-Rücksprung **9,3 s**.
#
# Darum wird NICHT beim ersten älteren Datensatz abgebrochen — das könnte einen
# in-Fenster-Datensatz übersehen, der im File hinter einem verirrten älteren
# steht. Erst wenn ``_DISORDER_MARGIN_RECORDS`` Datensätze älter als der Cutoff
# gesehen wurden, ist das Fenster nachweislich links geschlossen. 64 gibt gegen
# die gemessenen 7 Zeilen ~9x Marge.
#
# Grenze ehrlich benannt: ein Stream, dessen Datensätze um MEHR als diese Marge
# verschoben sind, bräuchte einen Full-Scan. Für die Audit-Streams hier ist das
# messbar nicht der Fall.
_DISORDER_MARGIN_RECORDS = 64


def iter_jsonl_since(
    path: Path,
    *,
    since: str,
    key: str,
    dict_only: bool = True,
    _chunk_bytes: int = _WINDOW_CHUNK_BYTES,
) -> Iterator[dict[str, Any]]:
    """Stream nur die Datensätze ab ``since`` — I/O proportional zum FENSTER.

    Für **zeitsortierte append-only** JSONL-Streams (jede Zeile trägt unter
    ``key`` einen lexikografisch vergleichbaren ISO-Zeitstempel). Gelesen wird
    vom Dateiende rückwärts in Blöcken, bis ein Eintrag VOR ``since`` auftaucht;
    geliefert wird wieder in chronologischer Reihenfolge.

    Motivation (2026-07-30): ``build_priority_gate_summary`` parste alle 88.314
    Zeilen von ``trading_loop_audit.jsonl`` (54,8 MB), um die 1.203 Zeilen der
    letzten 24 h zu zählen — 1,36 %. Das Verhältnis verschlechtert sich täglich,
    weil das Fenster fix ist und die Datei ewig wächst. Rotation wäre hier der
    falsche Hebel: der Stream speist Edge-/Verdikt-Rechnungen, seine Historie IST
    Evidenz und darf nicht gelöscht werden — also nicht kürzen, sondern gezielter
    lesen.

    **Vollständigkeit vor Sparsamkeit:** es gibt bewusst KEIN Zeilenlimit. Ein
    fixes ``tail=N`` würde bei einer Frequenz-Spitze still abschneiden und einen
    truth-tragenden Zähler verfälschen — die Schleife erweitert stattdessen
    solange nach hinten, bis das Fenster nachweislich links abgeschlossen ist
    (oder der Dateianfang erreicht wurde).

    Zeilen ohne ``key`` werden **behalten**, nicht verworfen: ein fehlendes Feld
    beweist nicht, dass der Eintrag alt ist (Legacy-Zeilen ohne Zeitstempel gibt
    es im Bestand). Der Aufrufer entscheidet. Korrupte Zeilen werden übersprungen
    — gleiche Policy wie :func:`iter_jsonl_tolerant`, ohne Retry.
    """
    if not path.exists():
        return
    try:
        size = path.stat().st_size
    except OSError:
        return
    if size <= 0:
        return

    # Rückwärts in Blöcken lesen, bis das Fenster links geschlossen ist.
    with path.open("rb") as handle:
        end = size
        chunks: list[bytes] = []
        window_closed = False
        older_seen = 0
        while end > 0 and not window_closed:
            start = max(0, end - max(1, _chunk_bytes))
            handle.seek(start)
            chunk = handle.read(end - start)
            chunks.insert(0, chunk)
            end = start
            if end == 0:
                break
            # Nur den NEUEN Block auszählen und den Zähler mitführen (O(Blöcke)
            # statt O(Blöcke²) — den akkumulierten Puffer jedes Mal neu zu
            # dekodieren kostete beim 72-h-Fenster den Grossteil der Laufzeit).
            #
            # Ab dem ersten Newline liegen ganze Zeilen vor; das angeschnittene
            # Stück am Blockanfang gehört in den vorherigen Block. Die letzte
            # Zeile des Blocks ist ihr eigener Anfang und dekodiert nicht — sie
            # wird schlicht nicht gezählt, was gegen eine Marge von 64 belanglos
            # ist (doppelt gezählt wird nichts).
            nl = chunk.find(b"\n")
            if nl < 0:
                continue
            for raw in chunk[nl + 1 :].split(b"\n"):
                stamp = _stamp_of(raw, key=key)
                if stamp is not None and stamp < since:
                    older_seen += 1
            if older_seen >= _DISORDER_MARGIN_RECORDS:
                window_closed = True

    text = b"".join(chunks).decode("utf-8", errors="replace")
    lines = text.split("\n")
    if end > 0:
        # Angeschnittene erste Zeile aus dem letzten gelesenen Block verwerfen.
        lines = lines[1:]

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if dict_only and not isinstance(obj, dict):
            continue
        stamp = obj.get(key)
        if isinstance(stamp, str) and stamp < since:
            continue
        yield obj


def _stamp_of(raw: bytes, *, key: str) -> str | None:
    """Zeitstempel einer Rohzeile, oder ``None`` wenn unlesbar/nicht vorhanden."""
    stripped = raw.strip()
    if not stripped:
        return None
    try:
        obj = json.loads(stripped.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    stamp = obj.get(key)
    return stamp if isinstance(stamp, str) else None
