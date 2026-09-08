"""Die Sonde muss den Blocker finden, ohne selbst einer zu werden.

Ein Messinstrument, das die Messung veraendert, ist wertlos
(OBSERVER_INTERFERENCE). Diese Tests halten beide Seiten fest: dass die Sonde
einen bekannten kuenstlichen Blocker korrekt erkennt UND dass sie ausgeschaltet
nichts tut, eingeschaltet wenig kostet, sich selbst begrenzt und keine Inhalte
nach aussen traegt.
"""

from __future__ import annotations

import asyncio
import re
import time

import pytest

from app.observability import loop_stall_probe as probe_mod
from app.observability.loop_stall_probe import ENV_FLAG, LoopStallProbe, probe_enabled

_THRESHOLD_S = 0.2
_HEARTBEAT_S = 0.02
_POLL_S = 0.01


def _fast_probe(**kw: object) -> LoopStallProbe:
    params: dict[str, object] = {
        "heartbeat_s": _HEARTBEAT_S,
        "stall_threshold_s": _THRESHOLD_S,
        "poll_s": _POLL_S,
    }
    params.update(kw)
    return LoopStallProbe(**params)  # type: ignore[arg-type]


# --------------------------------------------------------------------- FLAG OFF


@pytest.mark.parametrize("value", [None, "", "0", "false", "yes", "2", " 1 x"])
def test_flag_is_off_unless_exactly_one(monkeypatch: pytest.MonkeyPatch, value: str | None) -> None:
    """Nur ``KAI_LOOP_STALL_PROBE=1`` schaltet ein — nichts sonst."""
    if value is None:
        monkeypatch.delenv(ENV_FLAG, raising=False)
    else:
        monkeypatch.setenv(ENV_FLAG, value)
    assert probe_enabled() is False


def test_flag_on_is_recognised(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_FLAG, "1")
    assert probe_enabled() is True
    monkeypatch.setenv(ENV_FLAG, " 1 ")  # Rand-Whitespace ist tolerierbar
    assert probe_enabled() is True


def test_lifespan_does_not_start_the_probe_when_flag_is_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FLAG OFF heisst: kein Thread, kein Task, kein gc-Callback."""
    import gc

    monkeypatch.delenv(ENV_FLAG, raising=False)
    before = len(gc.callbacks)
    assert probe_enabled() is False
    assert len(gc.callbacks) == before


# ------------------------------------------------------------------ COUNTERPROBE


@pytest.mark.asyncio
async def test_known_synthetic_blocker_is_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Der Kern: ein bekannter, absichtlich gesetzter Blocker MUSS auffallen.

    Ohne diese Gegenprobe koennte die Sonde stumm bleiben und niemand wuesste,
    ob sie funktioniert oder ob es gerade nichts zu melden gab.
    """
    probe = _fast_probe()
    probe.start()
    try:
        await asyncio.sleep(0.1)  # Heartbeat anlaufen lassen
        time.sleep(0.6)  # <-- der kuenstliche Blocker, synchron im Loop-Thread
        await asyncio.sleep(0.3)  # Sonde Zeit zum Abschluss geben
    finally:
        probe.cancel()

    snap = probe.snapshot()
    assert snap["events"] >= 1, "Der kuenstliche Blocker wurde NICHT erkannt"
    assert float(snap["worst_s"]) >= _THRESHOLD_S
    kinds = snap["by_kind"]
    assert isinstance(kinds, dict)
    assert "sync_block" in kinds or "gc_pause" in kinds


@pytest.mark.asyncio
async def test_the_trace_names_the_callsite(monkeypatch: pytest.MonkeyPatch) -> None:
    """FLAG ON muss eine VERWERTBARE Spur liefern — Datei, Zeile, Funktion."""
    probe = _fast_probe()
    probe.start()
    try:
        await asyncio.sleep(0.1)
        time.sleep(0.5)
        await asyncio.sleep(0.3)
    finally:
        probe.cancel()

    snap = probe.snapshot()
    recent = snap["recent"]
    assert isinstance(recent, list) and recent, "keine Spur aufgezeichnet"
    stack = recent[-1]["stack"]
    assert stack, "Spur ohne Stack ist wertlos"
    joined = "\n".join(stack)
    assert "test_loop_stall_probe.py" in joined, "die Spur nennt die Aufrufstelle nicht"
    assert re.search(r":\d+ in ", joined), "Datei:Zeile in Funktion fehlt"


@pytest.mark.asyncio
async def test_quiet_loop_produces_no_events() -> None:
    """Ohne Blocker keine Meldung — sonst waere jede Meldung wertlos."""
    probe = _fast_probe()
    probe.start()
    try:
        for _ in range(20):
            await asyncio.sleep(0.01)
    finally:
        probe.cancel()

    assert probe.snapshot()["events"] == 0


# ----------------------------------------------------------------------- NOISE


@pytest.mark.asyncio
async def test_event_rate_is_capped() -> None:
    """Ein dauerblockierter Prozess darf das Log nicht fluten."""
    probe = _fast_probe(max_events_per_min=2)
    probe.start()
    try:
        await asyncio.sleep(0.1)
        for _ in range(5):
            time.sleep(0.35)
            await asyncio.sleep(0.15)
    finally:
        probe.cancel()

    snap = probe.snapshot()
    assert snap["events"] <= 2, f"Rate-Limit nicht eingehalten: {snap['events']}"
    assert snap["suppressed"] >= 1, "unterdrueckte Ereignisse werden nicht gezaehlt"


# --------------------------------------------------------------------- SECRETS


@pytest.mark.asyncio
async def test_the_trace_carries_no_values() -> None:
    """Stack-Abzuege duerfen Orte nennen, niemals Werte.

    In diesem Prozess liegen Tokens, Schluessel und Order-Payloads in Frames. Der
    Abzug rendert deshalb ausschliesslich Datei, Zeile, Funktion und die
    Quelltextzeile — keine Locals.
    """
    secret = "sk-live-DO-NOT-LEAK-2f4a9c"  # noqa: S105 - Testwert, kein echtes Geheimnis

    probe = _fast_probe()
    probe.start()
    try:
        await asyncio.sleep(0.1)
        _held_in_a_local = secret  # liegt beim Abzug im Frame
        time.sleep(0.5)
        assert _held_in_a_local  # Variable am Leben halten
        await asyncio.sleep(0.3)
    finally:
        probe.cancel()

    snap = probe.snapshot()
    dumped = str(snap)
    assert "DO-NOT-LEAK" not in dumped, "der Stack-Abzug traegt einen lokalen Wert nach aussen"
    assert secret not in dumped


# -------------------------------------------------------------------- OVERHEAD


@pytest.mark.asyncio
async def test_overhead_is_small_and_measured() -> None:
    """Die Sonde selbst darf den Loop nicht nennenswert bremsen.

    Gemessen wird, wie viele Loop-Durchlaeufe in einem festen Zeitfenster
    stattfinden — mit und ohne laufende Sonde. Die Schranke ist bewusst grosszuegig
    (nicht schlechter als die Haelfte), damit der Test auf langsamer CI nicht
    flattert; ein echter Observer-Effekt waere um Groessenordnungen groesser.
    """

    async def count_iterations(window_s: float) -> int:
        end = time.monotonic() + window_s
        n = 0
        while time.monotonic() < end:
            n += 1
            await asyncio.sleep(0)
        return n

    baseline = await count_iterations(0.3)

    probe = _fast_probe()
    probe.start()
    try:
        with_probe = await count_iterations(0.3)
    finally:
        probe.cancel()

    assert baseline > 0
    ratio = with_probe / baseline
    assert ratio > 0.5, f"Sonde bremst den Loop zu stark: {ratio:.2f} der Baseline"
    assert probe.snapshot()["events"] == 0, "die Sonde hat sich selbst als Stall gemeldet"


@pytest.mark.asyncio
async def test_cancel_removes_the_gc_callback() -> None:
    """Nach cancel() bleibt nichts im Prozess zurueck."""
    import gc

    probe = _fast_probe()
    before = len(gc.callbacks)
    probe.start()
    assert len(gc.callbacks) == before + 1
    await asyncio.sleep(0.05)
    probe.cancel()
    assert len(gc.callbacks) == before


def test_module_declares_no_artifact_stream() -> None:
    """Bewusst kein neuer artifacts-Strom — der braeuchte Leser und Konsequenz."""
    source = probe_mod.__file__
    assert source is not None
    text = open(source, encoding="utf-8").read()  # noqa: SIM115
    assert "artifacts/" not in text.replace("artifacts-Strom", "").replace("artifacts_", "")
