"""Unit tests for the auto-reprocess pre-step in the premium healthcheck script.

The script lives outside the ``app/`` package (``scripts/premium_pipeline_healthcheck.py``)
and is executed by a systemd timer every 60 s. The pre-step nudges the
envelope-to-paper bridge so re-pending envelopes (e.g. signals whose first
auto-fill tick saw a transient market-data miss) get re-processed without an
operator clicking "Reprocess Bridge".

Why these tests:
- 2026-05-14 forensik: BAS/USDT was stuck re-pending for 10h17m, ASTER/USDT
  for 1m38s — both unblocked only by manual Reprocess clicks. Auto-reprocess
  closes that gap, but must (a) be disable-able, (b) never crash the
  healthcheck if run_tick itself misbehaves, (c) stay quiet on an empty bus
  so the systemd journal does not get spammed.
"""
from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path

import pytest

from app.execution.envelope_to_paper_bridge import BridgeTickResult

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "premium_pipeline_healthcheck.py"
)


@pytest.fixture
def healthcheck_module():
    """Import the script as a module so we can monkeypatch its globals."""
    spec = importlib.util.spec_from_file_location(
        "premium_healthcheck_under_test", _SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["premium_healthcheck_under_test"] = module
    spec.loader.exec_module(module)
    try:
        yield module
    finally:
        sys.modules.pop("premium_healthcheck_under_test", None)


def test_auto_reprocess_disabled_via_env_skips_run_tick(
    monkeypatch: pytest.MonkeyPatch, healthcheck_module
):
    """KAI_HEALTHCHECK_AUTO_REPROCESS=0 must short-circuit before run_tick."""
    monkeypatch.setenv("KAI_HEALTHCHECK_AUTO_REPROCESS", "0")
    calls = {"n": 0}

    async def fake_run_tick():  # noqa: ANN202 — signature match only
        calls["n"] += 1
        return BridgeTickResult(enabled=True)

    monkeypatch.setattr(healthcheck_module, "run_tick", fake_run_tick)
    healthcheck_module._auto_reprocess_pending()
    assert calls["n"] == 0, "run_tick must not be invoked when toggle is 0"


def test_auto_reprocess_swallows_run_tick_exception(
    monkeypatch: pytest.MonkeyPatch, healthcheck_module, caplog
):
    """A flaky run_tick must NOT crash the healthcheck — log + continue."""
    monkeypatch.setenv("KAI_HEALTHCHECK_AUTO_REPROCESS", "1")

    async def boom():
        raise RuntimeError("market_data_provider_flapping")

    monkeypatch.setattr(healthcheck_module, "run_tick", boom)
    caplog.set_level(logging.WARNING, logger="premium-healthcheck")
    # Must not raise — that would prevent compute_pipeline_health from running.
    healthcheck_module._auto_reprocess_pending()
    assert any(
        "auto-reprocess tick failed" in rec.message
        and "market_data_provider_flapping" in rec.message
        for rec in caplog.records
    ), f"expected warning log, got: {[r.message for r in caplog.records]}"


def test_auto_reprocess_emits_info_when_envelopes_scanned(
    monkeypatch: pytest.MonkeyPatch, healthcheck_module, caplog
):
    """When the bridge actually did work, log it for journal traceability."""
    monkeypatch.setenv("KAI_HEALTHCHECK_AUTO_REPROCESS", "1")

    async def fake_tick():
        return BridgeTickResult(
            enabled=True,
            envelopes_scanned=2,
            filled=1,
            re_pending=1,
            expired=0,
        )

    monkeypatch.setattr(healthcheck_module, "run_tick", fake_tick)
    caplog.set_level(logging.INFO, logger="premium-healthcheck")
    healthcheck_module._auto_reprocess_pending()
    matched = [
        rec.message
        for rec in caplog.records
        if "auto-reprocess tick scanned=2" in rec.message and "filled=1" in rec.message
    ]
    assert matched, (
        "expected info log with scan/fill counts, got: "
        f"{[r.message for r in caplog.records]}"
    )


def test_auto_reprocess_stays_silent_on_empty_bus(
    monkeypatch: pytest.MonkeyPatch, healthcheck_module, caplog
):
    """Quiet bus = no INFO log (avoid journal spam every 60 s)."""
    monkeypatch.setenv("KAI_HEALTHCHECK_AUTO_REPROCESS", "1")

    async def fake_tick():
        return BridgeTickResult(enabled=True, envelopes_scanned=0)

    monkeypatch.setattr(healthcheck_module, "run_tick", fake_tick)
    caplog.set_level(logging.INFO, logger="premium-healthcheck")
    healthcheck_module._auto_reprocess_pending()
    info_records = [
        rec
        for rec in caplog.records
        if rec.levelno >= logging.INFO and "auto-reprocess tick" in rec.message
    ]
    assert not info_records, (
        "empty bus should not log INFO — would spam journal once per minute"
    )


def test_auto_reprocess_handles_disabled_bridge(
    monkeypatch: pytest.MonkeyPatch, healthcheck_module
):
    """Bridge globally disabled (operator_signal_bridge_enabled=False) is fine."""
    monkeypatch.setenv("KAI_HEALTHCHECK_AUTO_REPROCESS", "1")

    async def fake_tick():
        return BridgeTickResult(enabled=False)

    monkeypatch.setattr(healthcheck_module, "run_tick", fake_tick)
    # No raise, no crash.
    healthcheck_module._auto_reprocess_pending()
