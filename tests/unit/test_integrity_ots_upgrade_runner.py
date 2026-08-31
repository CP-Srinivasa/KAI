"""Unit tests for the L3 OTS upgrade runner (scripts/integrity_ots_upgrade.py).

Covers the no-op gates (disabled / stamper!=opentimestamps) and that the runner
invokes the upgrade pass when armed. The upgrade internals are covered by
test_integrity_ots_upgrade.py — here only the thin runner wrapper + exit codes.
"""

from __future__ import annotations

import scripts.integrity_ots_upgrade as runner
from scripts.integrity_ots_upgrade import main

from app.core.integrity_settings import IntegritySettings
from app.integrity.upgrade import UpgradeReport


def test_runner_disabled_is_noop(monkeypatch) -> None:
    def _must_not_run(*a, **k):
        raise AssertionError("upgrade must not run when disabled")

    monkeypatch.setattr(runner, "upgrade_pending_proofs", _must_not_run)
    assert main(IntegritySettings(enabled=False)) == 0


def test_runner_null_stamper_is_noop(monkeypatch) -> None:
    def _must_not_run(*a, **k):
        raise AssertionError("upgrade must not run for stamper=null")

    monkeypatch.setattr(runner, "upgrade_pending_proofs", _must_not_run)
    assert main(IntegritySettings(enabled=True, stamper="null")) == 0


def test_runner_runs_upgrade_when_armed(monkeypatch, tmp_path) -> None:
    calls: list[str] = []

    def _fake_upgrade(proofs_dir):  # noqa: ANN001
        calls.append(str(proofs_dir))
        return UpgradeReport(scanned=2, upgraded=1, still_pending=1)

    monkeypatch.setattr(runner, "upgrade_pending_proofs", _fake_upgrade)
    rc = main(
        IntegritySettings(
            enabled=True, stamper="opentimestamps", proofs_dir=str(tmp_path / "proofs")
        )
    )
    assert rc == 0
    assert calls == [str(tmp_path / "proofs")]


def test_runner_reports_missing_library_as_error(monkeypatch) -> None:
    from app.integrity.anchor import AnchorUnavailableError

    def _boom(proofs_dir):  # noqa: ANN001
        raise AnchorUnavailableError("opentimestamps library not installed")

    monkeypatch.setattr(runner, "upgrade_pending_proofs", _boom)
    rc = main(IntegritySettings(enabled=True, stamper="opentimestamps"))
    assert rc == 1


def test_runner_fails_when_a_proof_could_not_be_upgraded(monkeypatch, tmp_path, capsys) -> None:
    """G6/A7-017: der Lauf meldete monatelang ``failed=1`` und endete mit 0.

    Die Unit blieb dadurch gruen und ``OnFailure`` feuerte nie — ein Beweis,
    der nicht mehr fortgeschrieben werden kann, sah aus wie ein
    fortgeschriebener. Ein ``SuccessExitStatus`` gab es in der Unit nie; der
    Fehler sass in der letzten Zeile des Skripts.
    """
    monkeypatch.setattr(
        runner,
        "upgrade_pending_proofs",
        lambda proofs_dir: UpgradeReport(scanned=143, upgraded=0, already_confirmed=142, failed=1),
    )
    rc = main(
        IntegritySettings(
            enabled=True, stamper="opentimestamps", proofs_dir=str(tmp_path / "proofs")
        )
    )
    assert rc == 1
    out = capsys.readouterr().out
    assert "failed=1" in out
    assert "FAILED" in out


def test_runner_stays_green_when_nothing_failed(monkeypatch, tmp_path) -> None:
    """Negativkontrolle: nach der Uebereignung am 31.08. lief derselbe Bestand
    mit ``failed=0`` durch — die Verschaerfung darf keinen Dauer-Alarm erzeugen."""
    monkeypatch.setattr(
        runner,
        "upgrade_pending_proofs",
        lambda proofs_dir: UpgradeReport(scanned=143, upgraded=1, already_confirmed=142, failed=0),
    )
    assert (
        main(
            IntegritySettings(
                enabled=True, stamper="opentimestamps", proofs_dir=str(tmp_path / "proofs")
            )
        )
        == 0
    )
