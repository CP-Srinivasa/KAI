"""Operator-CLI: wo steht der HOTP-Zaehler? (Befund LIVE-Fenster 2026-09-04)

**Warum es dieses Werkzeug gibt.** Im LIVE-Fenster scheiterten drei
HOTP-Versuche hintereinander, bevor die Freigabe klappte. Die Ursache war
banal und von aussen nicht unterscheidbar: der Eintrag im YubiKey trug ein
ANDERES Secret als der Seed auf dem Pi. Ein falscher Code und ein falsches
Secret sehen an der API identisch aus (beides ``approval refused``), und der
Operator hat waehrenddessen keinen Weg zu sehen, welchen Zaehler der Pi
ueberhaupt erwartet.

**Was das Skript deshalb NICHT tut.** Es liest den Seed nicht und gibt ihn
nicht aus — sonst waere die Diagnose eines Geheimnisses ein Grund, das
Geheimnis auf den Bildschirm zu holen. Es beantwortet genau eine Frage: welchen
Zaehler erwartet der naechste gueltige Code.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "hotp_check_counter.py"
SEED = "JBSWY3DPEHPK3PXP"


def _load() -> object:
    spec = importlib.util.spec_from_file_location("hotp_check_counter", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _bootstrapped(tmp_path: Path, next_counter: int) -> Path:
    from app.security.hotp_auth import bootstrap_hotp_journal

    journal = tmp_path / "hotp.jsonl"
    bootstrap_hotp_journal(journal, next_counter=next_counter)
    return journal


def test_it_reports_the_counter_the_next_code_must_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    journal = _bootstrapped(tmp_path, 0)
    monkeypatch.setattr(sys, "argv", ["hotp_check_counter.py", "--journal-path", str(journal)])

    assert _load().main() == 0  # type: ignore[attr-defined]

    out = capsys.readouterr().out
    assert "next_expected_counter=0" in out


def test_it_follows_the_journal_after_a_bootstrap_to_a_later_position(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    journal = _bootstrapped(tmp_path, 7)
    monkeypatch.setattr(sys, "argv", ["hotp_check_counter.py", "--journal-path", str(journal)])

    assert _load().main() == 0  # type: ignore[attr-defined]

    out = capsys.readouterr().out
    assert "next_expected_counter=7" in out
    assert "last_used_counter=6" in out


def test_it_never_prints_the_seed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Die Diagnose eines Geheimnisses ist kein Grund, es auf den Schirm zu holen."""
    journal = _bootstrapped(tmp_path, 3)
    seed_path = tmp_path / "hotp_seed.b32"
    seed_path.write_text(SEED + "\n", encoding="ascii")
    monkeypatch.setenv("APP_LN_HOTP_SEED_PATH", str(seed_path))
    monkeypatch.setattr(sys, "argv", ["hotp_check_counter.py", "--journal-path", str(journal)])

    assert _load().main() == 0  # type: ignore[attr-defined]

    captured = capsys.readouterr()
    assert SEED not in captured.out
    assert SEED not in captured.err


def test_an_uninitialised_journal_is_a_clear_refusal_not_a_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``0`` waere die gefaehrlichste Antwort: sie sieht aus wie eine frische Anlage.

    Ein nicht initialisiertes Journal heisst, dass ``hotp_bootstrap.py`` noch
    nicht gelaufen ist — und dann ist JEDE Freigabe blockiert, nicht der Zaehler
    falsch.
    """
    monkeypatch.setattr(
        sys, "argv", ["hotp_check_counter.py", "--journal-path", str(tmp_path / "none.jsonl")]
    )

    assert _load().main() == 2  # type: ignore[attr-defined]
    assert "bootstrap" in capsys.readouterr().err.lower()


def test_the_script_does_not_import_the_seed_reader() -> None:
    """Ein strukturelles Argument statt einer Zusicherung im Fliesstext."""
    source = _SCRIPT.read_text(encoding="utf-8")
    assert "_load_seed" not in source
    assert "hotp_seed" not in source.replace("APP_LN_HOTP_SEED_PATH", "")
