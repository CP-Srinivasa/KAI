"""Beobachtet Event-Loop-Stalls von AUSSERHALB des Loops.

``EventLoopLagSampler`` misst, DASS der Loop steht. Er kann nicht sagen, WORAN —
weil er selbst im Loop laeuft und waehrend des Stalls per Definition nicht
drankommt. Diese Sonde beobachtet aus einem eigenen Thread und nimmt im Moment
des Stalls einen Stack-Abzug des Loop-Threads. Erst das benennt den Verursacher,
statt ihn zu erraten.

Anlass (2026-09-08): Der Loop steht 13,5 % der Wanduhr (gemessen ueber die
Identitaet ``Summe lag = 60 - n`` je 60-s-Rollup: 97 s auf 720 s). Die Wiederkehr
liegt unregelmaessig um ~90 s und passt damit zu KEINEM konfigurierten Timer
(60/300/300/900/900 s). Vier Hypothesen wurden gemessen und verworfen:
RSS/feedparser (0 Polls im Fenster), ``source_map_load_failed`` (0 Treffer),
``/dashboard/api/edge-window`` (68 ms kalt) und die Portfolio-Frequenz (der Pfad
brach vor dem Symlink-Fix nach 52 ms ab, rechnete also gar nicht).

AUSDRUECKLICH EIN MESSINSTRUMENT:
  * default AUS, Einschalten nur ueber ``KAI_LOOP_STALL_PROBE=1``
  * keine Gegenmassnahme, kein Neustart, kein Alert, keine neue Alarmklasse
  * keine Timer-, Timeout- oder Scheduling-Aenderung
  * kein Einfluss auf Trading- oder Execution-Verhalten

Das Flag wird bewusst direkt aus der Umgebung gelesen und NICHT ueber
``app/core/settings.py`` gefuehrt: die Datei steht mit null Zeilen Headroom im
God-File-Ratchet, und ein Diagnose-Schalter rechtfertigt keine Baseline-Anhebung.
"""

from __future__ import annotations

import asyncio
import gc
import logging
import os
import sys
import threading
import time
import traceback
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import FrameType

logger = logging.getLogger(__name__)

ENV_FLAG = "KAI_LOOP_STALL_PROBE"

# Der Heartbeat muss dichter takten als die Stall-Schwelle, sonst meldet die Sonde
# ihre eigene Schlafpause als Stall. 100 ms gegen 500 ms Schwelle laesst vier
# Aufschlaege Spielraum.
DEFAULT_HEARTBEAT_S = 0.1
DEFAULT_STALL_THRESHOLD_S = 0.5
DEFAULT_POLL_S = 0.05
# BEWUSST KEIN eigener artifacts-Strom. `config/stream_contracts.json` verlangt
# fuer jeden neuen Strom einen Leser, eine Ausfallkonsequenz und ein Freshness-
# Fenster ("Ein neuer Strom ohne Konsument ist ein Produzent, kein System").
# Fuer ein temporaeres Messinstrument waeren alle drei Angaben gelogen: es liest
# niemand automatisch, nichts geht kaputt wenn es schweigt, und default laeuft es
# gar nicht. Also strukturiertes Logging plus In-Memory-Snapshot — beides braucht
# keinen Vertrag und verschwindet mit dem Prozess.

# NOISE-Grenze. Ein durchgehend blockierter Prozess darf die Platte nicht
# vollschreiben; die ersten Ereignisse tragen ohnehin die Information.
DEFAULT_MAX_EVENTS_PER_MIN = 20
DEFAULT_MAX_STACK_FRAMES = 25

# Stack-Frames aus diesen Modulen sind der Loop selbst im Leerlauf, nicht der
# Verursacher. Steht einer davon zuoberst, wartet der Loop regulaer auf I/O.
_IDLE_MARKERS = ("selectors.py", "asyncio/base_events.py", "asyncio\\base_events.py")


def probe_enabled() -> bool:
    """Nur ``KAI_LOOP_STALL_PROBE=1`` schaltet ein. Alles andere bleibt aus."""
    return os.environ.get(ENV_FLAG, "").strip() == "1"


@dataclass
class StallEvent:
    started_at_utc: str
    duration_s: float
    kind: str
    since_previous_s: float | None
    gc_running: bool
    gc_collections_during: int
    stack_signature: str
    stack: list[str] = field(default_factory=list)

    def to_json_dict(self) -> dict[str, object]:
        return {
            "started_at_utc": self.started_at_utc,
            "duration_s": round(self.duration_s, 4),
            "kind": self.kind,
            "since_previous_s": (
                None if self.since_previous_s is None else round(self.since_previous_s, 3)
            ),
            "gc_running": self.gc_running,
            "gc_collections_during": self.gc_collections_during,
            "stack_signature": self.stack_signature,
            "stack": self.stack,
        }


def _utc_now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def _format_frames(frame: FrameType | None, *, limit: int) -> list[str]:
    """Nur Datei, Zeile, Funktion und Quelltextzeile — NIEMALS lokale Variablen.

    ``traceback`` rendert per Default keine Locals; das bleibt bewusst so. Ein
    Stack-Abzug soll den Ort benennen, nicht Werte transportieren: in diesem
    Prozess liegen Tokens, Schluessel und Order-Payloads in Frames herum.
    """
    if frame is None:
        return []
    summary = traceback.extract_stack(frame)
    lines: list[str] = []
    for entry in summary[-limit:]:
        text = f"{entry.filename}:{entry.lineno} in {entry.name}"
        if entry.line:
            text += f" | {entry.line.strip()[:120]}"
        lines.append(text)
    return lines


def _signature(stack: list[str]) -> str:
    """Kurzkennung ohne Quelltext, damit Wiederholungen zusammenfallen."""
    tail = [line.split(" | ")[0] for line in stack[-5:]]
    return " <- ".join(reversed(tail)) if tail else "unknown"


def _classify(stack: list[str], *, gc_running: bool) -> str:
    if gc_running:
        return "gc_pause"
    if not stack:
        return "unknown"
    top = stack[-1]
    if any(marker in top for marker in _IDLE_MARKERS):
        # Der Loop haengt in seiner eigenen Warteschleife. Bei aktuellem Heartbeat
        # ist das normal; hier bedeutet es, dass der Heartbeat NICHT lief, obwohl
        # der Loop wartete — also ein langer Await ohne Fortschritt.
        return "long_await"
    return "sync_block"


class LoopStallProbe:
    """Heartbeat im Loop, Beobachter im Thread.

    Die teure Arbeit — Stack-Abzug, Formatierung, Schreiben — passiert
    ausschliesslich im Beobachter-Thread. Der Loop traegt nur einen
    ``monotonic()``-Zeitstempel, damit die Messung nicht selbst zur Ursache wird
    (OBSERVER_INTERFERENCE).
    """

    def __init__(
        self,
        *,
        heartbeat_s: float = DEFAULT_HEARTBEAT_S,
        stall_threshold_s: float = DEFAULT_STALL_THRESHOLD_S,
        poll_s: float = DEFAULT_POLL_S,
        max_events_per_min: int = DEFAULT_MAX_EVENTS_PER_MIN,
        max_stack_frames: int = DEFAULT_MAX_STACK_FRAMES,
    ) -> None:
        self.heartbeat_s = heartbeat_s
        self.stall_threshold_s = stall_threshold_s
        self.poll_s = poll_s
        self.max_events_per_min = max_events_per_min
        self.max_stack_frames = max_stack_frames

        self._last_beat = time.monotonic()
        self._beats = 0
        self._loop_thread_id: int | None = None
        self._task: asyncio.Task[None] | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._events: deque[StallEvent] = deque(maxlen=200)
        self._emitted: deque[float] = deque(maxlen=512)
        self._suppressed = 0
        self._last_stall_end: float | None = None
        self._gc_depth = 0
        self._gc_count_total = 0
        self._gc_callback_installed = False

    # ---------------------------------------------------------------- lifecycle

    def start(self) -> asyncio.Task[None]:
        if self._task is not None and not self._task.done():
            return self._task
        self._loop_thread_id = threading.get_ident()
        self._last_beat = time.monotonic()
        self._install_gc_callback()
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._watch, name="kai-loop-stall-probe", daemon=True
        )
        self._thread.start()
        self._task = asyncio.create_task(self._heartbeat(), name="kai-loop-stall-heartbeat")
        logger.info(
            "loop_stall_probe_started threshold_s=%.2f heartbeat_s=%.2f",
            self.stall_threshold_s,
            self.heartbeat_s,
        )
        return self._task

    def cancel(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._remove_gc_callback()

    # ---------------------------------------------------------------- heartbeat

    async def _heartbeat(self) -> None:
        """Das Billigste, was im Loop laufen kann: ein Zeitstempel."""
        while True:
            self._last_beat = time.monotonic()
            self._beats += 1
            await asyncio.sleep(self.heartbeat_s)

    # ------------------------------------------------------------------ watcher

    def _watch(self) -> None:
        while not self._stop.wait(self.poll_s):
            age = time.monotonic() - self._last_beat
            if age < self.stall_threshold_s:
                continue
            self._capture(age)

    def _capture(self, initial_age: float) -> None:
        """Stack im Moment des Stalls greifen, dann dessen Ende abwarten."""
        gc_before = self._gc_count_total
        gc_running = self._gc_depth > 0
        stack: list[str] = []
        if self._loop_thread_id is not None:
            frame = sys._current_frames().get(self._loop_thread_id)
            stack = _format_frames(frame, limit=self.max_stack_frames)

        started = time.monotonic() - initial_age
        # Ende abwarten, um die WAHRE Dauer zu bekommen — nicht nur die Schwelle.
        while not self._stop.is_set():
            if time.monotonic() - self._last_beat < self.stall_threshold_s:
                break
            if time.monotonic() - started > 120.0:  # Reissleine
                break
            self._stop.wait(self.poll_s)
        duration = self._last_beat - started

        gap = None if self._last_stall_end is None else started - self._last_stall_end
        self._last_stall_end = self._last_beat

        if not self._allow_emit():
            self._suppressed += 1
            return

        event = StallEvent(
            started_at_utc=_utc_now_iso(),
            duration_s=max(0.0, duration),
            kind=_classify(stack, gc_running=gc_running),
            since_previous_s=gap,
            gc_running=gc_running,
            gc_collections_during=max(0, self._gc_count_total - gc_before),
            stack_signature=_signature(stack),
            stack=stack,
        )
        self._events.append(event)
        self._emit(event)

    def _allow_emit(self) -> bool:
        now = time.monotonic()
        while self._emitted and now - self._emitted[0] > 60.0:
            self._emitted.popleft()
        if len(self._emitted) >= self.max_events_per_min:
            return False
        self._emitted.append(now)
        return True

    # ----------------------------------------------------------------------- gc

    def _install_gc_callback(self) -> None:
        if self._gc_callback_installed:
            return
        gc.callbacks.append(self._on_gc)
        self._gc_callback_installed = True

    def _remove_gc_callback(self) -> None:
        if not self._gc_callback_installed:
            return
        try:
            gc.callbacks.remove(self._on_gc)
        except ValueError:  # pragma: no cover - schon entfernt
            pass
        self._gc_callback_installed = False

    def _on_gc(self, phase: str, _info: dict[str, int]) -> None:
        # Bewusst minimal: zwei Integer-Operationen. Der Callback laeuft MIT der
        # GIL waehrend der Sammlung; alles Teurere waere hier selbst der Blocker.
        if phase == "start":
            self._gc_depth += 1
        else:
            self._gc_depth = max(0, self._gc_depth - 1)
            self._gc_count_total += 1

    # ------------------------------------------------------------------ output

    def _emit(self, event: StallEvent) -> None:
        """Strukturiert loggen. Kein Artefakt-Strom, kein Alert, keine Metrik.

        WARNING, damit es im Betriebslog auffaellt, ohne eine eigene Alarmklasse
        aufzumachen — die Sonde laeuft ohnehin nur, wenn jemand sie einschaltet.
        """
        logger.warning(
            "loop_stall kind=%s duration_s=%.3f since_previous_s=%s gc=%s sig=%s",
            event.kind,
            event.duration_s,
            "n/a" if event.since_previous_s is None else f"{event.since_previous_s:.1f}",
            event.gc_running,
            event.stack_signature,
        )
        for line in event.stack[-8:]:
            logger.warning("loop_stall_frame %s", line)

    # ------------------------------------------------------------------ readout

    def snapshot(self) -> dict[str, object]:
        events = list(self._events)
        return {
            "enabled": True,
            "beats": self._beats,
            "events": len(events),
            "suppressed": self._suppressed,
            "threshold_s": self.stall_threshold_s,
            "by_kind": {
                kind: sum(1 for e in events if e.kind == kind)
                for kind in sorted({e.kind for e in events})
            },
            "worst_s": max((e.duration_s for e in events), default=0.0),
            "recent": [e.to_json_dict() for e in events[-5:]],
        }
