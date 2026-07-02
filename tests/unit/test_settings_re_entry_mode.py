"""Tests for the D-191 re-entry-mode capability gate.

The gate is a hard fail-closed boot guard: when
``RE_ENTRY_MODE_ENABLED=1`` *every* selected ``enforce_*`` invariant
must hold or AppSettings refuses to instantiate. These tests cover each
branch in isolation so a regression on one invariant is caught without
ambiguity.

Default behaviour (``enabled=false``) must keep boot identical to today --
covered by the disabled-no-op test.
"""

from __future__ import annotations

import pytest

from app.core.errors import ConfigurationError
from app.core.re_entry_mode import ReEntryModeProfile
from app.core.settings import (
    AlertSettings,
    AppSettings,
    TelegramChannelIngestSettings,
    TradingViewSettings,
)


def _disabled_profile() -> ReEntryModeProfile:
    """Default -- no enforcement at all."""
    return ReEntryModeProfile(enabled=False)


def _enabled_profile(**overrides: bool) -> ReEntryModeProfile:
    """Master switch on; individual flags overridable."""
    base = {
        "enabled": True,
        "enforce_provenance_secret": False,
        "enforce_replay_cache_persistent": False,
        "enforce_replay_cache_absolute_path": False,
        "enforce_watchdog_heartbeat": False,
        "enforce_observability_complete": False,
    }
    base.update(overrides)
    return ReEntryModeProfile(**base)


def test_disabled_profile_is_a_no_op() -> None:
    # The gate's whole purpose is to be invisible until explicitly armed.
    # With enabled=false even an empty provenance_secret + non-persistent
    # replay cache must not block boot -- that's the laptop's daily state.
    settings = AppSettings(
        re_entry_mode=_disabled_profile(),
        alerts=AlertSettings(provenance_secret=""),
        tradingview=TradingViewSettings(
            webhook_replay_cache_persistent=False,
            webhook_replay_cache_db_path="artifacts/replay.db",
        ),
        telegram_channel_ingest=TelegramChannelIngestSettings(heartbeat_path=""),
    )
    assert settings.re_entry_mode.enabled is False


def test_enabled_with_all_flags_off_is_also_a_no_op() -> None:
    # Operator can flip the master switch but stage individual invariants.
    # All-off is legal; nothing should trip.
    settings = AppSettings(
        re_entry_mode=_enabled_profile(),
        alerts=AlertSettings(provenance_secret=""),
        tradingview=TradingViewSettings(
            webhook_replay_cache_persistent=False,
            webhook_replay_cache_db_path="rel/replay.db",
        ),
        telegram_channel_ingest=TelegramChannelIngestSettings(heartbeat_path=""),
    )
    assert settings.re_entry_mode.enabled is True


def test_provenance_secret_missing_blocks_boot() -> None:
    # S-001: HMAC seal needs a secret. Empty string must hard-fail.
    with pytest.raises(ConfigurationError, match="ALERT_PROVENANCE_SECRET"):
        AppSettings(
            re_entry_mode=_enabled_profile(enforce_provenance_secret=True),
            alerts=AlertSettings(provenance_secret="   "),
        )


def test_provenance_secret_present_passes() -> None:
    settings = AppSettings(
        re_entry_mode=_enabled_profile(enforce_provenance_secret=True),
        alerts=AlertSettings(provenance_secret="real-secret"),
    )
    assert settings.alerts.provenance_secret == "real-secret"


def test_replay_cache_persistent_false_blocks_boot() -> None:
    # S-002: a non-persistent cache loses state across restarts and
    # re-opens the replay window. Refuse to boot.
    with pytest.raises(ConfigurationError, match="WEBHOOK_REPLAY_CACHE_PERSISTENT"):
        AppSettings(
            re_entry_mode=_enabled_profile(enforce_replay_cache_persistent=True),
            tradingview=TradingViewSettings(webhook_replay_cache_persistent=False),
        )


def test_replay_cache_persistent_true_passes() -> None:
    settings = AppSettings(
        re_entry_mode=_enabled_profile(enforce_replay_cache_persistent=True),
        tradingview=TradingViewSettings(webhook_replay_cache_persistent=True),
    )
    assert settings.tradingview.webhook_replay_cache_persistent is True


def test_replay_cache_relative_path_blocks_boot() -> None:
    # S-002b: relative paths break under systemd/Pi where cwd is unknown.
    with pytest.raises(ConfigurationError, match="not absolute"):
        AppSettings(
            re_entry_mode=_enabled_profile(enforce_replay_cache_absolute_path=True),
            tradingview=TradingViewSettings(
                webhook_replay_cache_db_path="artifacts/replay.db",
            ),
        )


def test_replay_cache_absolute_path_passes(tmp_path) -> None:
    abs_db = str(tmp_path / "replay.db")
    settings = AppSettings(
        re_entry_mode=_enabled_profile(
            enforce_replay_cache_absolute_path=True,
        ),
        tradingview=TradingViewSettings(webhook_replay_cache_db_path=abs_db),
    )
    assert settings.tradingview.webhook_replay_cache_db_path == abs_db


def test_watchdog_heartbeat_empty_path_blocks_boot() -> None:
    # S-003: without a configured heartbeat path the worker has nothing
    # to touch; canonical_read can't observe liveness.
    with pytest.raises(ConfigurationError, match="TELEGRAM_CHANNEL_HEARTBEAT_PATH"):
        AppSettings(
            re_entry_mode=_enabled_profile(enforce_watchdog_heartbeat=True),
            telegram_channel_ingest=TelegramChannelIngestSettings(
                heartbeat_path="",
            ),
        )


def test_watchdog_heartbeat_path_set_passes() -> None:
    settings = AppSettings(
        re_entry_mode=_enabled_profile(enforce_watchdog_heartbeat=True),
        telegram_channel_ingest=TelegramChannelIngestSettings(
            heartbeat_path="artifacts/heartbeat.txt",
        ),
    )
    assert settings.telegram_channel_ingest.heartbeat_path == "artifacts/heartbeat.txt"


def test_observability_enforce_blocks_boot_until_b002_lands() -> None:
    # B-002 capability flag is hard-coded False. Flipping the enforce
    # switch must boot-fail -- that's the whole point of opt-in here.
    with pytest.raises(ConfigurationError, match="B-002"):
        AppSettings(
            re_entry_mode=_enabled_profile(
                enforce_observability_complete=True,
            ),
        )


def test_multiple_violations_aggregate_in_error_message() -> None:
    # Ergonomics: one boot, all violations. Operator shouldn't have to
    # restart five times to see the full TODO list.
    with pytest.raises(ConfigurationError) as excinfo:
        AppSettings(
            re_entry_mode=_enabled_profile(
                enforce_provenance_secret=True,
                enforce_replay_cache_persistent=True,
                enforce_watchdog_heartbeat=True,
            ),
            alerts=AlertSettings(provenance_secret=""),
            tradingview=TradingViewSettings(
                webhook_replay_cache_persistent=False,
            ),
            telegram_channel_ingest=TelegramChannelIngestSettings(
                heartbeat_path="",
            ),
        )
    msg = str(excinfo.value)
    assert "PROVENANCE_SECRET" in msg
    assert "REPLAY_CACHE_PERSISTENT" in msg
    assert "TELEGRAM_CHANNEL_HEARTBEAT_PATH" in msg


def test_observability_default_off_does_not_break_full_enforce() -> None:
    # Bundle test: if the operator forgets to disable observability_complete
    # the gate must not fail because of it (default is off, B-002 isn't
    # ready). Only the *opted-in* invariants count.
    settings = AppSettings(
        re_entry_mode=ReEntryModeProfile(
            enabled=True,
            enforce_provenance_secret=True,
            enforce_replay_cache_persistent=True,
            enforce_replay_cache_absolute_path=False,
            enforce_watchdog_heartbeat=True,
        ),
        alerts=AlertSettings(provenance_secret="seal"),
        tradingview=TradingViewSettings(webhook_replay_cache_persistent=True),
        telegram_channel_ingest=TelegramChannelIngestSettings(
            heartbeat_path="artifacts/hb.txt",
        ),
    )
    assert settings.re_entry_mode.enforce_observability_complete is False
