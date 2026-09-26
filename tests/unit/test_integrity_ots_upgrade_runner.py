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


# --------------------------------------------------------------------------- #
# Bitcoin-Pruefung nach dem Upgrade (D-288, Befund 4)
# --------------------------------------------------------------------------- #


def _armed(tmp_path):  # noqa: ANN001, ANN202
    return IntegritySettings(
        enabled=True, stamper="opentimestamps", proofs_dir=str(tmp_path / "proofs")
    )


def test_runner_verifies_against_bitcoin_when_chain_is_enabled(
    monkeypatch, tmp_path, capsys
) -> None:
    from app.core.chain_settings import ChainSettings
    from app.integrity.bitcoin_verify import VerifyReport

    monkeypatch.setattr(runner, "upgrade_pending_proofs", lambda proofs_dir: UpgradeReport())
    seen: list[str] = []

    def _verify(proofs_dir, chain):  # noqa: ANN001, ANN202
        seen.append(str(proofs_dir))
        return VerifyReport(scanned=3, verified=3)

    monkeypatch.setattr(runner, "run_bitcoin_verification", _verify)
    rc = main(_armed(tmp_path), chain=ChainSettings(enabled=True))
    assert rc == 0
    assert seen == [str(tmp_path / "proofs")]
    assert "bitcoin_verify" in capsys.readouterr().out


def test_runner_fails_on_a_bitcoin_mismatch(monkeypatch, tmp_path, capsys) -> None:
    """Ein Proof, der eine Verankerung behauptet, die der Block nicht traegt, ist ein Befund."""
    from app.core.chain_settings import ChainSettings
    from app.integrity.bitcoin_verify import VerifyReport

    monkeypatch.setattr(runner, "upgrade_pending_proofs", lambda proofs_dir: UpgradeReport())
    monkeypatch.setattr(
        runner, "run_bitcoin_verification", lambda proofs_dir, chain: VerifyReport(mismatch=1)
    )
    rc = main(_armed(tmp_path), chain=ChainSettings(enabled=True))
    assert rc == 1
    assert "MISMATCH" in capsys.readouterr().out


def test_runner_skips_bitcoin_check_without_chain(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setattr(runner, "upgrade_pending_proofs", lambda proofs_dir: UpgradeReport())

    def _must_not_run(proofs_dir, chain):  # noqa: ANN001, ANN202
        raise AssertionError("no chain → no bitcoin verification")

    monkeypatch.setattr(runner, "run_bitcoin_verification", _must_not_run)
    assert main(_armed(tmp_path)) == 0
    assert "bitcoin_verify: skipped" in capsys.readouterr().out
