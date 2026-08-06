"""CLI wiring for the CORE8 forecaster panel (shadow epoch, #647 follow-up).

The engine (issue/resolve/status/verify) is fully tested in
test_forecaster_panel.py — these tests pin the OPERATOR SURFACE: exit codes,
timer idempotence (duplicate t0 is a no-op success, not a failed unit), the
fail-closed refusal of an incomplete anchor day, and that a provider failure
never leaves a partial store behind. No network: the provider builder is
monkeypatched in every test.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from typer.testing import CliRunner

from app.cli.commands import forecaster_cli
from app.cli.commands.trading import trading_app
from app.research.forecaster_panel import PANELS_FILENAME, RESOLUTIONS_FILENAME
from app.research.forecaster_resolvers import (
    BTC_SYMBOL,
    ETH_SYMBOL,
    DailyCandle,
    KlinesUnavailableError,
)

runner = CliRunner()

T0 = date(2026, 6, 1)


def _fixture_data() -> dict[str, dict[date, DailyCandle]]:
    """Deterministic candles [T0-200, T0+31] for BTC+ETH (mirrors engine tests)."""
    btc: dict[date, DailyCandle] = {}
    eth: dict[date, DailyCandle] = {}
    for i in range(-200, 32):
        day = T0 + timedelta(days=i)
        c_btc = Decimal(100) + Decimal((i * 37) % 25) - Decimal(12)
        c_eth = Decimal(50) + Decimal((i * 29) % 21) - Decimal(10)
        btc[day] = DailyCandle(day=day, close=c_btc, low=c_btc - 2, volume=Decimal(1000))
        eth[day] = DailyCandle(day=day, close=c_eth, low=c_eth - 1, volume=Decimal(500))
    return {BTC_SYMBOL: btc, ETH_SYMBOL: eth}


def _install_provider(
    monkeypatch: pytest.MonkeyPatch,
    data: dict[str, dict[date, DailyCandle]] | None = None,
) -> None:
    payload = data if data is not None else _fixture_data()

    def fetch(symbol: str, start: date, end: date) -> dict[date, DailyCandle]:
        return {d: c for d, c in payload.get(symbol, {}).items() if start <= d <= end}

    monkeypatch.setattr(forecaster_cli, "_build_provider", lambda: fetch)


def _install_failing_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    def fetch(symbol: str, start: date, end: date) -> dict[date, DailyCandle]:
        raise KlinesUnavailableError("venue down")

    monkeypatch.setattr(forecaster_cli, "_build_provider", lambda: fetch)


def _issue(tmp_path: Path, *extra: str) -> object:
    return runner.invoke(
        trading_app,
        ["forecaster-issue", "--t0", T0.isoformat(), "--store-dir", str(tmp_path), *extra],
    )


# --------------------------------------------------------------------------- #
# forecaster-issue
# --------------------------------------------------------------------------- #


def test_issue_writes_one_chained_panel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install_provider(monkeypatch)
    result = _issue(tmp_path, "--json")
    assert result.exit_code == 0, result.output
    lines = (tmp_path / PANELS_FILENAME).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert '"panel_index":0' in lines[0].replace(" ", "")
    assert '"p_kai": null' in lines[0] or '"p_kai":null' in lines[0].replace(" ", "")


def test_issue_duplicate_t0_is_noop_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Persistent=true timers can double-fire — the second run must NOT fail."""
    _install_provider(monkeypatch)
    assert _issue(tmp_path).exit_code == 0
    second = _issue(tmp_path)
    assert second.exit_code == 0, second.output
    assert "bereits" in second.output
    lines = (tmp_path / PANELS_FILENAME).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1


def test_issue_refuses_incomplete_anchor_day(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """t0 >= today (UTC) has no complete daily candle — fail closed, write nothing."""
    _install_provider(monkeypatch)
    today = datetime.now(UTC).date()
    result = runner.invoke(
        trading_app,
        ["forecaster-issue", "--t0", today.isoformat(), "--store-dir", str(tmp_path)],
    )
    assert result.exit_code == 2, result.output
    assert not (tmp_path / PANELS_FILENAME).exists()


def test_issue_provider_failure_exits_1_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_failing_provider(monkeypatch)
    result = _issue(tmp_path)
    assert result.exit_code == 1, result.output
    assert not (tmp_path / PANELS_FILENAME).exists()


def test_issue_default_t0_is_yesterday_utc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    yesterday = datetime.now(UTC).date() - timedelta(days=1)
    seen: list[date] = []

    def fetch(symbol: str, start: date, end: date) -> dict[date, DailyCandle]:
        seen.append(end)
        return {}

    monkeypatch.setattr(forecaster_cli, "_build_provider", lambda: fetch)
    result = runner.invoke(trading_app, ["forecaster-issue", "--store-dir", str(tmp_path)])
    # All questions become data-gap INVALID (empty provider) but the anchor is
    # yesterday and the panel is still issued append-only.
    assert result.exit_code == 0, result.output
    assert seen and seen[0] == yesterday


# --------------------------------------------------------------------------- #
# forecaster-resolve
# --------------------------------------------------------------------------- #


def test_resolve_writes_due_and_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_provider(monkeypatch)
    assert _issue(tmp_path).exit_code == 0

    first = runner.invoke(
        trading_app, ["forecaster-resolve", "--store-dir", str(tmp_path), "--json"]
    )
    assert first.exit_code == 0, first.output
    resolutions = (tmp_path / RESOLUTIONS_FILENAME).read_text(encoding="utf-8").splitlines()
    assert len(resolutions) > 0

    second = runner.invoke(
        trading_app, ["forecaster-resolve", "--store-dir", str(tmp_path), "--json"]
    )
    assert second.exit_code == 0, second.output
    assert '"written": 0' in second.output
    after = (tmp_path / RESOLUTIONS_FILENAME).read_text(encoding="utf-8").splitlines()
    assert len(after) == len(resolutions)


def test_resolve_empty_store_is_noop_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_provider(monkeypatch)
    result = runner.invoke(trading_app, ["forecaster-resolve", "--store-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output


# --------------------------------------------------------------------------- #
# forecaster-status / forecaster-verify
# --------------------------------------------------------------------------- #


def test_status_reports_counters_and_t0_coverage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_provider(monkeypatch)
    assert _issue(tmp_path).exit_code == 0
    result = runner.invoke(
        trading_app, ["forecaster-status", "--store-dir", str(tmp_path), "--json"]
    )
    assert result.exit_code == 0, result.output
    assert '"panels": 1' in result.output
    assert '"t0_first": "2026-06-01"' in result.output
    assert '"t0_missing_days": 0' in result.output


def test_verify_ok_and_tamper_detection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install_provider(monkeypatch)
    assert _issue(tmp_path).exit_code == 0

    ok = runner.invoke(trading_app, ["forecaster-verify", "--store-dir", str(tmp_path)])
    assert ok.exit_code == 0, ok.output

    panels = tmp_path / PANELS_FILENAME
    tampered = panels.read_text(encoding="utf-8").replace('"sealed":false', '"sealed":true')
    panels.write_text(tampered, encoding="utf-8")
    broken = runner.invoke(trading_app, ["forecaster-verify", "--store-dir", str(tmp_path)])
    assert broken.exit_code == 1, broken.output
    assert "panel_hash_mismatch" in broken.output
