"""Timer-Scheduleability-Sonde: kein Befund aus einer Momentaufnahme (V4).

Vorfall 2026-09-15: ``timer_scheduleability`` meldete um 17:30Z
``kai-technical-screener.timer`` und um 23:16Z ``kai-funding-refresh.timer`` als
"feuern nie wieder" (P1, kritisch). Beide feuerten nachweislich weiter --
LastTrigger am 16.09. um 08:13:33 bzw. 08:28:49 CEST, beide mit gesetztem
``NextElapseUSecMonotonic``.

Ursache ist ein TOCTOU in der Sonde selbst: sie fragt systemd ZWEIMAL
nacheinander -- erst die Timer-Fakten, dann den ``ActiveState`` der ausgeloesten
Services -- und vergleicht die beiden Aufnahmen. Laeuft der Oneshot waehrend der
ersten Abfrage (Timer meldet ``infinity``, siehe ``has_future_trigger``) und ist
er bei der zweiten schon fertig, sieht die Sonde "kein Termin" UND "Service
laeuft nicht" und meldet einen toten Timer, der gerade eben gefeuert hat.

Die Heilung ist dieselbe wie bei ``process_runtime_probe``: ein Befund muss eine
zweite, zeitlich getrennte Messung ueberstehen. Ein wirklich toter Timer
(``kai-tv-auto-promote``, 2026-07-12 bis 2026-08-19 fuenf Wochen stumm) ist in
beiden Messungen terminlos und bleibt gemeldet.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.alerts import timer_schedule_probe as probe

SCREENER = "kai-technical-screener.timer"
FUNDING = "kai-funding-refresh.timer"


def _timer_block(unit: str, *, monotonic: str, realtime: str = "") -> str:
    service = unit.removesuffix(".timer") + ".service"
    return (
        f"NextElapseUSecRealtime={realtime}\n"
        f"NextElapseUSecMonotonic={monotonic}\n"
        "LastTriggerUSec=Wed 2026-09-16 06:13:33 UTC\n"
        f"Id={unit}\n"
        "UnitFileState=enabled\n"
        "ActiveState=active\n"
        f"Unit={service}\n"
    )


def _service_block(unit: str, *, state: str) -> str:
    service = unit.removesuffix(".timer") + ".service"
    return f"Id={service}\nActiveState={state}\n"


def _join(*blocks: str) -> str:
    return "\n".join(blocks)


class _FakeSystemctl:
    """Spielt eine Folge von ``systemctl show``-Antworten ab.

    Jede Messung besteht aus genau zwei Aufrufen: Timer-Fakten (erkennbar an
    ``-p Unit``), dann Service-Zustand. Die Reihenfolge ist die der Sonde.
    """

    def __init__(self, answers: list[tuple[str, int]]) -> None:
        self._answers = list(answers)
        self.calls: list[list[str]] = []

    def __call__(self, args, **_kwargs) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(args))
        if not self._answers:
            raise AssertionError(f"unerwarteter systemctl-Aufruf: {args}")
        stdout, code = self._answers.pop(0)
        return subprocess.CompletedProcess(args, code, stdout=stdout, stderr="")


@pytest.fixture
def sleeps(monkeypatch) -> list[float]:
    recorded: list[float] = []
    monkeypatch.setattr(probe, "_sleep", recorded.append)
    monkeypatch.delenv("KAI_TIMER_SCHEDULE_PROBE", raising=False)
    return recorded


def _install(monkeypatch, fake: _FakeSystemctl) -> None:
    monkeypatch.setattr(probe, "_run", fake)


# --------------------------------------------------------------------------
# Der Vorfall
# --------------------------------------------------------------------------


def test_oneshot_endet_zwischen_den_abfragen_ergibt_keinen_befund(monkeypatch, sleeps) -> None:
    """Genau der Fehlalarm vom 15.09.: erste Messung sieht einen scheinbar toten
    Timer, die zweite sieht ihn wieder terminiert."""
    fake = _FakeSystemctl(
        [
            # Messung 1: Timer mitten im Lauf -> infinity; Service schon fertig.
            (_timer_block(SCREENER, monotonic="infinity"), 0),
            (_service_block(SCREENER, state="inactive"), 0),
            # Messung 2: systemd hat nach Ende des Oneshots neu terminiert.
            (_timer_block(SCREENER, monotonic="3month 3w 4h 20min"), 0),
            (_service_block(SCREENER, state="inactive"), 0),
        ]
    )
    _install(monkeypatch, fake)

    assert probe.unscheduled_timer_finding() is None
    assert sleeps == [probe.RESAMPLE_DELAY_SEC]
    assert len(fake.calls) == 4


def test_wirklich_toter_timer_bleibt_gemeldet(monkeypatch, sleeps) -> None:
    """kai-tv-auto-promote: fuenf Wochen ``enabled``+``active``+``infinity``.
    Die Bestaetigungsmessung darf diese Erkennung nicht verlieren."""
    dead = _timer_block(SCREENER, monotonic="infinity")
    idle = _service_block(SCREENER, state="inactive")
    _install(monkeypatch, _FakeSystemctl([(dead, 0), (idle, 0), (dead, 0), (idle, 0)]))

    finding = probe.unscheduled_timer_finding()

    assert finding is not None
    assert finding.startswith("1 wiederkehrende Timer laufen ohne naechsten Termin")
    assert SCREENER in finding
    assert "sie feuern nie wieder" in finding


def test_gemeldet_wird_nur_die_schnittmenge_beider_messungen(monkeypatch, sleeps) -> None:
    fake = _FakeSystemctl(
        [
            (
                _join(
                    _timer_block(SCREENER, monotonic="infinity"),
                    _timer_block(FUNDING, monotonic="infinity"),
                ),
                0,
            ),
            (
                _join(
                    _service_block(SCREENER, state="inactive"),
                    _service_block(FUNDING, state="inactive"),
                ),
                0,
            ),
            (
                _join(
                    _timer_block(SCREENER, monotonic="1h 2min"),
                    _timer_block(FUNDING, monotonic="infinity"),
                ),
                0,
            ),
            (
                _join(
                    _service_block(SCREENER, state="inactive"),
                    _service_block(FUNDING, state="inactive"),
                ),
                0,
            ),
        ]
    )
    _install(monkeypatch, fake)

    finding = probe.unscheduled_timer_finding()

    assert finding is not None
    assert FUNDING in finding
    assert SCREENER not in finding
    assert finding.startswith("1 wiederkehrende Timer")


# --------------------------------------------------------------------------
# Kosten und Bestandsverhalten
# --------------------------------------------------------------------------


def test_ohne_kandidaten_keine_zweite_messung(monkeypatch, sleeps) -> None:
    """Der Normalfall darf nichts extra kosten -- keine Pause, keine Nachfrage."""
    fake = _FakeSystemctl(
        [
            (_timer_block(SCREENER, monotonic="1h"), 0),
            (_service_block(SCREENER, state="inactive"), 0),
        ]
    )
    _install(monkeypatch, fake)

    assert probe.unscheduled_timer_finding() is None
    assert sleeps == []
    assert len(fake.calls) == 2


def test_laufender_service_bleibt_entschuldigt(monkeypatch, sleeps) -> None:
    """Bestandsverhalten: ``kai-shadow-resolver`` laeuft fast die halbe Zeit und
    steht waehrenddessen regulaer ohne Termin."""
    fake = _FakeSystemctl(
        [
            (_timer_block(SCREENER, monotonic="infinity"), 0),
            (_service_block(SCREENER, state="activating"), 0),
        ]
    )
    _install(monkeypatch, fake)

    assert probe.unscheduled_timer_finding() is None
    assert sleeps == []


# --------------------------------------------------------------------------
# Fail-soft (Lehre #718): die Sonde ist kein Abbruchgrund
# --------------------------------------------------------------------------


def test_scheitert_die_zweite_messung_gibt_es_keinen_befund(monkeypatch, sleeps) -> None:
    """Ein unbestaetigter Befund ist keiner -- lieber ein Zyklus spaeter als ein
    geratener P1."""
    fake = _FakeSystemctl(
        [
            (_timer_block(SCREENER, monotonic="infinity"), 0),
            (_service_block(SCREENER, state="inactive"), 0),
            ("", 1),
        ]
    )
    _install(monkeypatch, fake)

    assert probe.unscheduled_timer_finding() is None


def test_scheitert_die_erste_messung_gibt_es_keinen_befund(monkeypatch, sleeps) -> None:
    _install(monkeypatch, _FakeSystemctl([("", 1)]))

    assert probe.unscheduled_timer_finding() is None
    assert sleeps == []


def test_systemctl_nicht_aufrufbar_gibt_keinen_befund(monkeypatch, sleeps) -> None:
    def _boom(*_a, **_k):
        raise OSError("systemctl fehlt")

    monkeypatch.setattr(probe, "_run", _boom)

    assert probe.unscheduled_timer_finding() is None
    assert sleeps == []


def test_abschaltbar_ueber_die_umgebung(monkeypatch, sleeps) -> None:
    fake = _FakeSystemctl([])
    _install(monkeypatch, fake)
    monkeypatch.setenv("KAI_TIMER_SCHEDULE_PROBE", "off")

    assert probe.unscheduled_timer_finding() is None
    assert fake.calls == []


def test_ohne_timer_dateien_wird_systemd_nicht_befragt(monkeypatch, sleeps, tmp_path: Path) -> None:
    fake = _FakeSystemctl([])
    _install(monkeypatch, fake)

    assert probe.unscheduled_timer_finding(timer_dir=tmp_path) is None
    assert fake.calls == []


def test_abfragen_laufen_in_utc(monkeypatch, sleeps) -> None:
    """``CEST`` ist nicht zurueckparsbar -- jede Abfrage muss ``TZ=UTC`` tragen."""
    seen_env: list[dict] = []

    def _capture(args, **kwargs):
        seen_env.append(kwargs.get("env") or {})
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(probe, "_run", _capture)
    probe.unscheduled_timer_finding()

    assert seen_env, "systemctl wurde nicht befragt"
    assert all(env.get("TZ") == "UTC" for env in seen_env)
