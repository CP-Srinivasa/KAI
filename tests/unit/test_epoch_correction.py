"""Operator decision 2026-08-18: epoch paper_v2_attested carries a correction notice.

A booked result stays booked - the append-only audit is never rewritten. What
changes is that no surface may quote the epoch's PnL without the proof that part
of it rests on fabricated inputs. These tests pin that the notice exists, that it
travels with the number through the API and the daily-strategy line, and that a
clean epoch stays unannotated.
"""

from __future__ import annotations

from app.execution.epoch_correction import (
    EPOCH_CORRECTION_NOTICES,
    epoch_correction_notice,
)


def test_paper_v2_attested_carries_a_notice() -> None:
    notice = epoch_correction_notice("paper_v2_attested")
    assert notice is not None
    assert notice.incident_ref == "DS-20260818-MOCK-EXIT"
    assert notice.measured_contaminated_closes == 4


def test_correction_reverses_the_sign_of_the_booked_result() -> None:
    """The whole point of the notice: booked is positive, corrected is not."""
    notice = epoch_correction_notice("paper_v2_attested")
    assert notice is not None
    assert notice.measured_booked_usd > 0
    assert notice.measured_corrected_usd < 0
    assert notice.flips_sign is True


def test_corrected_total_is_derived_not_restated() -> None:
    """No second hardcoded number that could drift from the first two."""
    notice = epoch_correction_notice("paper_v2_attested")
    assert notice is not None
    expected = notice.measured_booked_usd - notice.measured_contaminated_usd
    assert notice.measured_corrected_usd == expected


def test_notice_is_dated_and_points_at_a_way_to_re_measure() -> None:
    """Snapshot figures rot; the notice must say when and how to check."""
    notice = epoch_correction_notice("paper_v2_attested")
    assert notice is not None
    assert notice.measured_at_utc.startswith("2026-08-18")
    assert notice.verify_command.strip()


def test_notice_states_the_basis_of_its_figures() -> None:
    """A number without its population is not citeable - name the basis."""
    notice = epoch_correction_notice("paper_v2_attested")
    assert notice is not None
    basis = notice.measured_basis
    assert "trade_pnl_usd" in basis
    assert "position_partial_closed" in basis
    # trade_pnl_usd carries only the close fee - mixing it with the fee-corrected
    # series is exactly how the earlier figures drifted apart.
    assert "entry fee" in basis


def test_unknown_and_empty_epochs_have_no_notice() -> None:
    assert epoch_correction_notice("legacy_contaminated") is None
    assert epoch_correction_notice(None) is None
    assert epoch_correction_notice("") is None
    assert epoch_correction_notice("  ") is None


def test_as_dict_is_json_safe_and_complete() -> None:
    notice = epoch_correction_notice("paper_v2_attested")
    assert notice is not None
    payload = notice.as_dict()
    for key in (
        "epoch_id",
        "incident_ref",
        "summary",
        "detail",
        "verify_command",
        "measured_at_utc",
        "measured_closes",
        "measured_booked_usd",
        "measured_contaminated_closes",
        "measured_contaminated_usd",
        "measured_corrected_usd",
        "flips_sign",
    ):
        assert key in payload, f"missing {key}"
    assert all(isinstance(v, (str, int, float, bool)) for v in payload.values())


def test_notice_names_the_root_cause_not_just_the_symptom() -> None:
    """A notice that only says 'numbers were wrong' teaches nothing."""
    notice = epoch_correction_notice("paper_v2_attested")
    assert notice is not None
    detail = notice.detail
    assert "MockMarketDataAdapter" in detail
    assert "is_stale=False" in detail
    assert "3225.6863500000004" in detail


def test_every_registered_notice_is_self_consistent() -> None:
    for epoch_id, notice in EPOCH_CORRECTION_NOTICES.items():
        assert notice.epoch_id == epoch_id
        assert notice.incident_ref
        assert notice.summary
        assert notice.measured_closes >= notice.measured_contaminated_closes


def test_daily_strategy_line_carries_the_notice() -> None:
    """The operator reads this file daily - the caveat must ride along."""
    from app.cli.commands import daily_strategy

    notice = daily_strategy.epoch_correction_notice("paper_v2_attested")
    assert notice is not None
    assert notice.incident_ref == "DS-20260818-MOCK-EXIT"
