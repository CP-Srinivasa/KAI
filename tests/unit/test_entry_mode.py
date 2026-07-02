"""Entry-Safety-Mode (Goal 2026-06-01): EntryMode enum + ExecutionSettings wiring.

Verifies the kill-switch semantics and the fail-closed live-consistency guardrail.
Behavior under test (not implementation):
- only DISABLED stops autonomous loop entries; all other modes allow them;
- is_live correctly identifies the two live entry modes;
- the deployed default is never live_normal;
- a live entry mode on a non-live execution venue is rejected (fail-closed).
"""

from __future__ import annotations

import pytest

from app.core.enums import EntryMode


class TestEntryModeEnum:
    def test_loop_closed_in_disabled_and_limited_paper_modes(self) -> None:
        # Sprint S3 (#181): the two explicit limited paper modes keep the
        # AUTONOMOUS loop closed just like disabled — they open only their
        # named bridge/feeder routes (per-route truth in entry_policy).
        for mode in (
            EntryMode.DISABLED,
            EntryMode.PAPER_PREMIUM_LIMITED,
            EntryMode.PAPER_LEARNING,
        ):
            assert mode.allows_autonomous_loop_entry is False, mode
        for mode in (
            EntryMode.PAPER,
            EntryMode.PROBE,
            EntryMode.LIVE_LIMITED,
            EntryMode.LIVE_NORMAL,
        ):
            assert mode.allows_autonomous_loop_entry is True, mode

    def test_is_live_identifies_live_modes(self) -> None:
        assert EntryMode.LIVE_LIMITED.is_live is True
        assert EntryMode.LIVE_NORMAL.is_live is True
        for mode in (EntryMode.DISABLED, EntryMode.PAPER, EntryMode.PROBE):
            assert mode.is_live is False, mode

    def test_all_seven_modes_present(self) -> None:
        assert {m.value for m in EntryMode} == {
            "disabled",
            "paper_premium_limited",
            "paper_learning",
            "paper",
            "probe",
            "live_limited",
            "live_normal",
        }

    def test_is_paper_learning_covers_paper_modes_never_live_or_disabled(self) -> None:
        """Goal 2026-06-10 (+S3): paper-learning context = any mode that opens
        SOME risk-increasing entries on a non-live route — never disabled (no
        entries) and never the two live modes."""
        assert EntryMode.PAPER.is_paper_learning is True
        assert EntryMode.PROBE.is_paper_learning is True
        assert EntryMode.PAPER_PREMIUM_LIMITED.is_paper_learning is True
        assert EntryMode.PAPER_LEARNING.is_paper_learning is True
        assert EntryMode.DISABLED.is_paper_learning is False
        assert EntryMode.LIVE_LIMITED.is_paper_learning is False
        assert EntryMode.LIVE_NORMAL.is_paper_learning is False


class TestEntryModeSettings:
    def _settings(self):
        # import inside the test so the autouse cache-clear fixture applies
        from app.core.settings import get_settings

        return get_settings()

    def test_default_entry_mode_is_paper_never_live(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("EXECUTION_ENTRY_MODE", raising=False)
        em = self._settings().execution.entry_mode
        assert em == EntryMode.PAPER
        # The migration default must never be a live cadence.
        assert em not in (EntryMode.LIVE_LIMITED, EntryMode.LIVE_NORMAL)

    def test_disabled_mode_loads(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EXECUTION_ENTRY_MODE", "disabled")
        assert self._settings().execution.entry_mode == EntryMode.DISABLED

    def test_probe_mode_loads(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EXECUTION_ENTRY_MODE", "probe")
        assert self._settings().execution.entry_mode == EntryMode.PROBE

    def test_live_entry_mode_on_paper_venue_is_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Fail-closed: a live entry cadence cannot run on a non-live venue.
        monkeypatch.setenv("EXECUTION_ENTRY_MODE", "live_normal")
        monkeypatch.setenv("EXECUTION_MODE", "paper")
        with pytest.raises(ValueError, match="requires EXECUTION_MODE=live"):
            self._settings()

    def test_live_limited_on_paper_venue_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EXECUTION_ENTRY_MODE", "live_limited")
        monkeypatch.setenv("EXECUTION_MODE", "paper")
        with pytest.raises(ValueError, match="requires EXECUTION_MODE=live"):
            self._settings()
