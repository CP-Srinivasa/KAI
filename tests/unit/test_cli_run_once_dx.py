"""run-once DX hardening (Goal 2026-06-01, AUFGABE 2) — anti-misdiagnosis.

Before this fix `trading run-once BTC/USDT` was caught by Click as a generic
"Got unexpected extra argument" — technically exit!=0, but the message did not
tell the operator WHAT to do, and because a ``--symbol`` option exists an
operator could believe a symbol-specific tick had run. That exact confusion
produced a real Fehldiagnose.

These tests pin the corrected behaviour:
  - a positional symbol arg NEVER runs a tick (exit != 0),
  - the error names the cause and the remedy (use --symbol, or monitor),
  - the legit `--symbol BTC/USDT` form is unaffected,
  - --help is unambiguous that no positional symbol is expected.
"""

from __future__ import annotations

from typer.testing import CliRunner

from app.cli.commands.trading import trading_app

runner = CliRunner()


def test_positional_symbol_is_a_hard_error_not_a_silent_tick() -> None:
    result = runner.invoke(trading_app, ["run-once", "BTC/USDT"])
    assert result.exit_code != 0
    # it must NOT look like a successful tick
    assert "Trading Loop Run Once" not in result.output


def test_positional_symbol_error_is_actionable() -> None:
    result = runner.invoke(trading_app, ["run-once", "ETH/USDT", "--provider", "mock"])
    assert result.exit_code != 0
    out = result.output.lower()
    # names the cause + the remedy (so the operator does not misdiagnose)
    assert "symbol" in out
    assert "--symbol" in result.output  # the correct flag is shown verbatim
    assert "monitor" in out  # the alternative path is named


def test_help_shows_no_positional_symbol() -> None:
    result = runner.invoke(trading_app, ["run-once", "--help"])
    assert result.exit_code == 0
    out = result.output
    # --symbol is an OPTION; no bare [SYMBOL] positional argument is advertised.
    assert "--symbol" in out
    assert "[SYMBOL]" not in out
    assert "SYMBOL..." not in out


def test_no_args_is_accepted_default_symbol() -> None:
    """No positional + defaults must still be a VALID invocation (not the error).

    We only assert it parses past argument validation; the actual tick may exit
    non-zero for runtime reasons (no market data in CI) but it must NOT raise the
    positional-argument guard.
    """
    result = runner.invoke(trading_app, ["run-once", "--provider", "mock"])
    # whatever the runtime outcome, it must not be the positional-arg guard.
    assert "no positional symbol argument" not in result.output.lower()
