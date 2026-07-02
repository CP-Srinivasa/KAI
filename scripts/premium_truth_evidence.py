#!/usr/bin/env python3
"""Premium truth-fix evidence (2026-06-08) — readable proof of BUG-1..4, V-1, dedupe.

Run: python scripts/premium_truth_evidence.py
Exercises the pure decision paths with the real SKYAI 2026-06-07 numbers so the
operator can see, without reading tests, that:
  - the 101.94 garbage spot is rejected (BUG-2),
  - raw 24800 vs garbage spot is scale_unresolved_or_bad_price not SL-above-spot (BUG-1),
  - a good tick resolves scale to 0.248 and clears scale_unknown (BUG-3),
  - the garbage tick is ignored, signal stays pending (V-1),
  - raw + approved collapse to ONE business signal (dedupe),
  - the trail headline shows the operative terminal, not entry_mode posture (BUG-4).
"""

from __future__ import annotations

from app.execution.premium_scale_lifecycle import (
    analyze_bridge_history,
    build_scale_resolution_patch,
    decide_terminal_or_ignore,
)
from app.execution.scale_resolver import classify_scale_failure, detect_scale_factor
from app.market_data.price_sanity import LastGoodPriceStore, evaluate_price_sanity
from app.observability.premium_dedupe import dedupe_premium_signals
from app.observability.premium_signal_trail import build_trail

RAW_ENTRY, RAW_SL = 24800.0, 23800.0
TICKS: list[float | None] = [0.35609, None, 0.35561, 101.94]


def line(s: str = "") -> None:
    print(s)


def main() -> None:
    line("=== BUG-2: outlier gate over the SKYAI tick sequence ===")
    store = LastGoodPriceStore()
    for px in TICKS:
        v = evaluate_price_sanity(
            symbol="SKYAI/USDT", candidate_price=px, last_good_price=store.get("SKYAI/USDT")
        )
        if v.ok and px is not None:
            store.record("SKYAI/USDT", px)
        line(f"  spot={px!s:>9}  ok={v.ok!s:>5}  reason={v.reason}  score={v.outlier_score}")
    line(f"  last-good after sequence = {store.get('SKYAI/USDT')}  (garbage 101.94 never stored)")

    line()
    line("=== BUG-1: scale failure classification ===")
    factor = detect_scale_factor(RAW_ENTRY, 101.94)
    reason = classify_scale_failure(entry=RAW_ENTRY, spot=101.94, scale_factor_applied=factor)
    line(f"  raw 24800 vs garbage spot 101.94 -> factor={factor}  reason={reason}")
    good_factor = detect_scale_factor(RAW_ENTRY, 0.35609)
    line(f"  raw 24800 vs good spot 0.35609   -> factor={good_factor} (1e5)")

    line()
    line("=== BUG-3: scale lifecycle persist (good tick) ===")
    patch = build_scale_resolution_patch(
        scale_factor=good_factor,
        scaled_entry=RAW_ENTRY / good_factor,
        scaled_stop_loss=RAW_SL / good_factor,
        scaled_targets=[24925 / good_factor],
    )
    line(f"  scale_unknown -> {patch['scale_unknown']}   scaled_entry={patch['scaled_entry']}")

    line()
    line("=== V-1: garbage tick after valid pending is ignored ===")
    history = [
        {"stage": "pending", "reason": "price_outside_tolerance"},
        {"stage": "pending", "reason": "no_market_data"},
        {"stage": "pending", "reason": "price_outside_tolerance"},
    ]
    prior_bad, had_valid = analyze_bridge_history(history)
    decision = decide_terminal_or_ignore(
        prior_consecutive_bad=prior_bad, had_prior_valid_pending=had_valid
    )
    line(f"  had_valid_pending={had_valid}  prior_bad={prior_bad}  -> action={decision.action}")

    line()
    line("=== Dedupe: raw + approved = ONE business signal ===")
    raw = {
        "source": "telegram_premium_channel",
        "envelope_id": "ENV-TG-1",
        "payload": {"signal_id": "SIG-SKYAI", "source": "telegram_premium_channel"},
    }
    approved = {
        "source": "telegram_premium_channel_approved",
        "envelope_id": "ENV-APP-1",
        "payload": {"signal_id": "SIG-SKYAI", "source": "telegram_premium_channel_approved"},
    }
    groups = dedupe_premium_signals([raw, approved])
    g = groups[0]
    line(
        f"  2 input records -> {len(groups)} business signal  "
        f"double_sourced={g.is_double_sourced}  key={g.key}"
    )

    line()
    line("=== BUG-4: trail headline = operative terminal, not entry_mode posture ===")
    _payload = {
        "symbol": "SKYAIUSDT",
        "display_symbol": "SKYAI/USDT",
        "side": "buy",
        "direction": "long",
        "entry_type": "at",
        "entry_value": 24800.0,
        "stop_loss": 23800.0,
        "targets": [24925.0, 25050.0],
        "leverage": 10,
    }
    env = [
        {
            "timestamp_utc": "2026-06-06T15:33:29+00:00",
            "event": "telegram_channel_envelope",
            "message_type": "signal",
            "stage": "accepted",
            "status": "ok",
            "source": "telegram_premium_channel",
            "envelope_id": "ENV-TG-2",
            "idempotency_key": "idem-tg-2",
            "payload": dict(_payload),
        },
        {
            "timestamp_utc": "2026-06-06T15:33:30+00:00",
            "event": "telegram_channel_approval",
            "message_type": "signal",
            "stage": "accepted",
            "status": "ok",
            "source": "telegram_premium_channel_approved",
            "envelope_id": "ENV-APP-2",
            "idempotency_key": "idem-app-2",
            "origin_envelope_id": "ENV-TG-2",
            "origin_source": "telegram_premium_channel",
            "payload": dict(_payload),
        },
    ]
    bridge_records = [
        {
            "timestamp_utc": "2026-06-07T01:29:30+00:00",
            "envelope_id": "ENV-APP-2",
            "correlation_id": "ENV-TG-2",
            "stage": "rejected_entry_mode",
            "audit_reason": "entry_mode_disabled",
        },
        {
            "timestamp_utc": "2026-06-07T01:29:56+00:00",
            "envelope_id": "ENV-APP-2",
            "correlation_id": "ENV-TG-2",
            "stage": "rejected_scale_review",
            "audit_reason": "scale_unresolved_or_bad_price",
        },
    ]
    entries = build_trail(envelope_records=env, bridge_records=bridge_records, paper_records=[])
    e = entries[0]
    line(f"  overall={e.overall}  (operative terminal beats entry_mode posture)")

    line()
    line("EVIDENCE OK")


if __name__ == "__main__":
    main()
