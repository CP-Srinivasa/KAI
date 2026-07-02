"""CLI wiring for ``trading canonical-edge`` attest/verify/until (B5b).

Confirms the operator-facing surface: ``--attest`` seals a pinned payload,
``--verify <seq>`` recomputes it and returns exit 0/1, and a bad ``--until`` is
rejected. Full pinning/verification semantics live in test_edge_attestation.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from app.cli.commands.trading import trading_app

runner = CliRunner()

_LOOP = '{"status": "completed", "started_at": "2026-06-27T15:00:00+00:00"}\n'
_EXEC = (
    '{"event_type": "position_closed", "symbol": "BTC/USDT", "position_side": "long",'
    ' "entry_price": 100.0, "exit_price": 101.0, "quantity": 1.0, "reason": "tp",'
    ' "trade_pnl_usd": 1.0, "fee_usd": 0.1, "timestamp_utc": "2026-06-27T15:10:00+00:00",'
    ' "signal_source": "autonomous_generator"}\n'
)


def _dataset(tmp_path: Path) -> tuple[Path, Path, Path]:
    loop = tmp_path / "loop.jsonl"
    execp = tmp_path / "exec.jsonl"
    ledger = tmp_path / "truth.jsonl"
    loop.write_text(_LOOP, encoding="utf-8")
    execp.write_text(_EXEC, encoding="utf-8")
    return loop, execp, ledger


def test_attest_then_verify_roundtrip(tmp_path: Path) -> None:
    loop, execp, ledger = _dataset(tmp_path)
    attested = runner.invoke(
        trading_app,
        [
            "canonical-edge",
            "--loop-audit-path",
            str(loop),
            "--exec-audit-path",
            str(execp),
            "--ledger-path",
            str(ledger),
            "--attest",
            "--json",
        ],
    )
    assert attested.exit_code == 0, attested.output
    assert ledger.exists()

    verified = runner.invoke(
        trading_app, ["canonical-edge", "--verify", "1", "--ledger-path", str(ledger)]
    )
    assert verified.exit_code == 0, verified.output
    assert "VERIFY OK seq=1" in verified.output


def test_verify_missing_seq_exits_1(tmp_path: Path) -> None:
    ledger = tmp_path / "truth.jsonl"
    ledger.write_text("", encoding="utf-8")
    result = runner.invoke(
        trading_app, ["canonical-edge", "--verify", "5", "--ledger-path", str(ledger)]
    )
    assert result.exit_code == 1
    assert "VERIFY FAIL" in result.output


def test_bad_until_is_rejected(tmp_path: Path) -> None:
    result = runner.invoke(trading_app, ["canonical-edge", "--until", "not-a-date"])
    assert result.exit_code == 2  # click BadParameter
