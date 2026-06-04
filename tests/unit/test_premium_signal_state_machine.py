from app.premium.state_machine import (
    PremiumSignalState,
    bridge_stage_to_state,
    close_reason_to_state,
    normalized_source,
    origin_signal_id,
    state_tone,
)


def test_accepted_is_not_green() -> None:
    assert state_tone(PremiumSignalState.ENVELOPE_ACCEPTED) == "warn"
    assert state_tone(PremiumSignalState.APPROVED) == "warn"


def test_execution_success_and_failures_have_distinct_tones() -> None:
    assert state_tone(PremiumSignalState.POSITION_OPEN) == "pos"
    assert state_tone(PremiumSignalState.CLOSED_TP) == "pos"
    assert state_tone(PremiumSignalState.ENTRY_DISABLED) == "neg"
    assert state_tone(PremiumSignalState.CLOSED_SL) == "neg"
    assert state_tone(PremiumSignalState.REQUIRES_REVIEW) == "warn"
    assert state_tone("ENTRY_DISABLED") == "neg"
    assert PremiumSignalState.POSITION_OPEN.value == "position_open"


def test_bridge_stage_mapping_separates_entry_disabled_risk_and_scale() -> None:
    assert bridge_stage_to_state("rejected_entry_mode") == PremiumSignalState.ENTRY_DISABLED
    assert bridge_stage_to_state("rejected_risk") == PremiumSignalState.RISK_REJECTED
    assert (
        bridge_stage_to_state("rejected_scale_review") == PremiumSignalState.REQUIRES_SCALE_REVIEW
    )
    assert bridge_stage_to_state("skipped_source") == PremiumSignalState.SOURCE_SKIPPED
    assert bridge_stage_to_state("pending") == PremiumSignalState.PENDING_ENTRY


def test_close_reason_mapping_keeps_sl_out_of_success_bucket() -> None:
    assert close_reason_to_state("tp_tier", realized_pnl_usd=4.2) == PremiumSignalState.CLOSED_TP
    assert close_reason_to_state("stop_loss", realized_pnl_usd=-1.0) == PremiumSignalState.CLOSED_SL
    assert close_reason_to_state("manual", realized_pnl_usd=0.0) == PremiumSignalState.CLOSED_MANUAL
    assert (
        close_reason_to_state("tp_tier", realized_pnl_usd=None) == PremiumSignalState.CLOSED_UNKNOWN
    )


def test_source_normalization_and_origin_identity() -> None:
    record = {
        "source": "telegram_premium_channel_approved",
        "origin_envelope_id": "ENV-origin",
        "envelope_id": "ENV-approved",
        "payload": {"source_uid": "telegram:-100:23878"},
    }
    assert normalized_source(record["source"]) == "telegram_premium_channel"
    assert origin_signal_id(record) == "telegram:-100:23878"
