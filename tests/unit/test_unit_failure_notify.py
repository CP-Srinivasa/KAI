"""Fehlgeschlagene systemd-Units müssen den Operator erreichen.

Befund 2026-08-17: 17 von 59 Units tragen ``ExecStart=-``. Der Bindestrich
unterdrückt den Fehlerstatus — die Unit gilt als ``success``, auch wenn das
Skript mit Exit != 0 endet. ``systemctl --failed`` bleibt leer, und ein
kaputter Job sieht exakt aus wie ein gesunder. Gleichzeitig trug **keine
einzige** Unit ein ``OnFailure=``: selbst eine ehrlich rot gewordene Unit hätte
niemanden erreicht.

Das ist dieselbe Familie wie der 6 Tage unbemerkte TV-Ingest-Tod: ein Zustand
wird korrekt berechnet und dann an niemanden zugestellt.

Der Notifier muss drei Dinge können, sonst schafft er ein neues Problem:
1. sagen, WELCHE Unit gescheitert ist und mit welchem Code,
2. Kontext mitliefern (letzte Journal-Zeilen), damit die Meldung handlungsfähig
   ist statt nur alarmierend,
3. bei einer dauerhaft scheiternden Unit NICHT im Minutentakt spammen.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from scripts.notify_unit_failure import build_message, should_send


def test_message_names_the_unit_and_the_exit_code() -> None:
    msg = build_message(
        "kai-technical-screener.service",
        exit_code="1",
        result="exit-code",
        journal_tail="Traceback (most recent call last):\nValueError: boom",
    )

    assert "kai-technical-screener.service" in msg
    assert "1" in msg
    assert "ValueError: boom" in msg


def test_message_survives_a_missing_journal() -> None:
    """No journal access must not swallow the alert itself."""
    msg = build_message("kai-x.service", exit_code="", result="", journal_tail="")

    assert "kai-x.service" in msg
    assert msg.strip() != ""


def test_message_truncates_a_runaway_journal() -> None:
    msg = build_message(
        "kai-x.service", exit_code="1", result="exit-code", journal_tail="x" * 10_000
    )

    # Telegram lehnt sehr lange Nachrichten ab — eine zu lange Meldung wäre
    # gar keine Meldung.
    assert len(msg) <= 3900


def test_first_failure_is_sent(tmp_path: Path) -> None:
    state = tmp_path / "state.json"

    assert should_send("kai-x.service", state_path=state, now=datetime(2026, 8, 17, tzinfo=UTC))


def test_repeat_failure_within_cooldown_is_suppressed(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    t0 = datetime(2026, 8, 17, tzinfo=UTC)

    assert should_send("kai-x.service", state_path=state, now=t0) is True
    assert should_send("kai-x.service", state_path=state, now=t0 + timedelta(minutes=5)) is False


def test_failure_after_cooldown_is_sent_again(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    t0 = datetime(2026, 8, 17, tzinfo=UTC)

    assert should_send("kai-x.service", state_path=state, now=t0) is True
    assert should_send("kai-x.service", state_path=state, now=t0 + timedelta(hours=7)) is True


def test_cooldown_is_per_unit_not_global(tmp_path: Path) -> None:
    """A noisy unit must never mute a different one."""
    state = tmp_path / "state.json"
    t0 = datetime(2026, 8, 17, tzinfo=UTC)

    assert should_send("kai-a.service", state_path=state, now=t0) is True
    assert should_send("kai-b.service", state_path=state, now=t0) is True


def test_corrupt_state_file_fails_open(tmp_path: Path) -> None:
    """When in doubt, alert. A broken state file must not silence failures."""
    state = tmp_path / "state.json"
    state.write_text("{not json", encoding="utf-8")

    assert should_send("kai-x.service", state_path=state, now=datetime(2026, 8, 17, tzinfo=UTC))
