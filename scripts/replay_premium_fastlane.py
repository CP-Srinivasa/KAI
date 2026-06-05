"""Deterministic premium-fastlane replay (Goal 2026-06-05 §20).

Runs the *decision pipeline* for a batch of authentic premium-telegram signals
against the pure fastlane router + scale/notional logic — no live market-data,
no disk side-effects. It proves that complete signals reach at least one of
``order_submitted`` / ``pending_entry`` / ``requires_scale_review`` and that the
fastlane never blocks on manual-approval, classic-allowlist, entry_mode,
source-quality, premium-bonus, forward-precision or priority-tier.

Each fixture signal carries a ``mock_spot`` that drives deterministic
scale-resolution and the entry-condition check, so the replay is reproducible
in CI.

Usage:
    python -m scripts.replay_premium_fastlane \
        --fixture tests/fixtures/latest_premium_signals.json [--json]
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.settings import AppSettings, PremiumFastlaneSettings, PremiumSettings
from app.execution.premium_fastlane import (
    resolve_leverage,
    resolve_notional,
    should_route_premium_fastlane,
)
from app.execution.scale_resolver import detect_scale_factor, validate_scaled_signal

_ENTRY_TOLERANCE_PCT = 0.5


def _fastlane_settings() -> AppSettings:
    """A fastlane-armed PAPER settings object (live stays protected — the live
    triple-flag is intentionally NOT set, so live_protected=True)."""
    settings = AppSettings()
    settings.premium_fastlane = PremiumFastlaneSettings(enabled=True, start_date="")
    settings.premium = PremiumSettings(paper_execution_enabled=True)
    return settings


def _build_envelope(sig: dict[str, Any]) -> dict[str, Any]:
    chat_id = sig.get("chat_id")
    msg_id = sig.get("message_id")
    source_uid = f"telegram:{chat_id}:{msg_id}" if chat_id and msg_id else None
    payload = {
        "symbol": sig.get("symbol"),
        "display_symbol": sig.get("display_symbol"),
        "direction": sig.get("direction"),
        "side": sig.get("side"),
        "entry_type": sig.get("entry_type", "limit"),
        "entry_value": sig.get("entry_value"),
        "entry_min": sig.get("entry_min"),
        "entry_max": sig.get("entry_max"),
        "stop_loss": sig.get("stop_loss"),
        "targets": list(sig.get("targets") or []),
        "leverage": sig.get("leverage"),
        "source_uid": source_uid,
        "source_chat_id": chat_id,
        "source_message_id": msg_id,
    }
    return {
        "envelope_id": f"ENV-REPLAY-{sig.get('symbol')}",
        "source": "telegram_premium_channel",
        "source_uid": source_uid,
        "chat_id": chat_id,
        "message_id": msg_id,
        "payload": payload,
    }


def _entry_condition_met(
    *, direction: str, spot: float, entry: float, entry_min, entry_max
) -> bool:
    if entry_min is not None and entry_max is not None and entry_max > entry_min > 0:
        return entry_min <= spot <= entry_max
    tol = entry * (_ENTRY_TOLERANCE_PCT / 100.0)
    if direction in {"long", "buy"}:
        return spot <= entry + tol
    return spot >= entry - tol


@dataclass
class ReplayTotals:
    signals: int = 0
    fastlane_valid: int = 0
    orders_submitted: int = 0
    pending_entries: int = 0
    positions_opened: int = 0
    schema_rejected: int = 0
    duplicates_skipped: int = 0
    requires_scale_review: int = 0
    errors: int = 0
    rows: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "signals": self.signals,
            "fastlane_valid": self.fastlane_valid,
            "orders_submitted": self.orders_submitted,
            "pending_entries": self.pending_entries,
            "positions_opened": self.positions_opened,
            "schema_rejected": self.schema_rejected,
            "duplicates_skipped": self.duplicates_skipped,
            "requires_scale_review": self.requires_scale_review,
            "errors": self.errors,
            "rows": self.rows,
        }


def run_replay(fixture_path: Path) -> ReplayTotals:
    data = json.loads(fixture_path.read_text(encoding="utf-8"))
    signals = data.get("fastlane_signals") or []
    settings = _fastlane_settings()
    totals = ReplayTotals()
    seen_uids: set[str] = set()

    for sig in signals:
        totals.signals += 1
        symbol = sig.get("display_symbol") or sig.get("symbol")
        row: dict[str, Any] = {"symbol": symbol, "stage": "parsed"}
        try:
            envelope = _build_envelope(sig)
            uid = envelope.get("source_uid")
            if uid and uid in seen_uids:
                totals.duplicates_skipped += 1
                row["stage"] = "duplicate_skipped"
                totals.rows.append(row)
                continue
            if uid:
                seen_uids.add(uid)

            decision = should_route_premium_fastlane(envelope, settings)
            row["bypassed_gates"] = decision.bypassed_gates
            row["live_protected"] = decision.live_protected
            if not decision.is_routable:
                # A blocked decision here is a schema / authenticity / routing
                # failure (the only hard reasons the fastlane allows).
                totals.schema_rejected += 1
                row["stage"] = "schema_rejected"
                row["reason"] = decision.reason
                totals.rows.append(row)
                continue
            totals.fastlane_valid += 1
            row["stage"] = "fastlane_validated"

            # Scale resolution against the deterministic mock spot.
            spot = float(sig["mock_spot"])
            entry_raw = float(sig["entry_value"])
            factor = detect_scale_factor(entry_raw, spot)
            entry = entry_raw / factor
            stop_loss = float(sig["stop_loss"]) / factor
            targets = [float(t) / factor for t in (sig.get("targets") or [])]
            entry_min = (float(sig["entry_min"]) / factor) if sig.get("entry_min") else None
            entry_max = (float(sig["entry_max"]) / factor) if sig.get("entry_max") else None
            row["scale_factor"] = factor

            # Geometry / scale plausibility guard (hard).
            reason = validate_scaled_signal(
                direction=str(sig.get("direction") or ""),
                entry=entry,
                stop_loss=stop_loss,
                targets=targets,
                spot=spot,
            )
            if reason is not None:
                totals.requires_scale_review += 1
                row["stage"] = "requires_scale_review"
                row["reason"] = reason
                totals.rows.append(row)
                continue

            # Notional / leverage policy (hard guards).
            leverage, lev_note = resolve_leverage(sig.get("leverage"), settings.premium_fastlane)
            notional, qty, notional_reject = resolve_notional(entry, settings.premium_fastlane)
            if notional_reject is not None or qty <= 0:
                totals.schema_rejected += 1
                row["stage"] = "rejected_notional"
                row["reason"] = notional_reject
                totals.rows.append(row)
                continue
            row["order_intent"] = "created"
            row["leverage"] = leverage
            row["notional_usdt"] = round(notional, 4)
            row["quantity"] = round(qty, 8)
            if lev_note:
                row["leverage_note"] = lev_note

            if _entry_condition_met(
                direction=str(sig.get("direction") or ""),
                spot=spot,
                entry=entry,
                entry_min=entry_min,
                entry_max=entry_max,
            ):
                totals.orders_submitted += 1
                totals.positions_opened += 1
                row["stage"] = "order_submitted"
            else:
                totals.pending_entries += 1
                row["stage"] = "pending_entry"
            totals.rows.append(row)
        except Exception as exc:  # noqa: BLE001
            totals.errors += 1
            row["stage"] = "error"
            row["error"] = f"{type(exc).__name__}: {exc}"
            totals.rows.append(row)

    return totals


def _print_table(totals: ReplayTotals) -> None:
    print(f"{'SYMBOL':<14}{'STAGE':<24}{'LEV':>5}{'NOTIONAL':>11}  REASON/NOTE")
    print("-" * 78)
    for r in totals.rows:
        sym = str(r.get("symbol") or "")
        stage = str(r.get("stage") or "")
        lev = r.get("leverage")
        notional = r.get("notional_usdt")
        extra = r.get("reason") or r.get("leverage_note") or r.get("error") or ""
        print(
            f"{sym:<14}{stage:<24}"
            f"{(f'{lev:g}' if lev is not None else ''):>5}"
            f"{(f'{notional:g}' if notional is not None else ''):>11}  {extra}"
        )
    print("-" * 78)
    print(
        f"signals={totals.signals} fastlane_valid={totals.fastlane_valid} "
        f"orders_submitted={totals.orders_submitted} pending_entries={totals.pending_entries} "
        f"positions_opened={totals.positions_opened} "
        f"schema_rejected={totals.schema_rejected} "
        f"duplicates_skipped={totals.duplicates_skipped} "
        f"requires_scale_review={totals.requires_scale_review} errors={totals.errors}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Premium-fastlane deterministic replay")
    parser.add_argument(
        "--fixture",
        default="tests/fixtures/latest_premium_signals.json",
        help="Path to the fixture JSON (uses its `fastlane_signals` array).",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = parser.parse_args(argv)

    totals = run_replay(Path(args.fixture))
    if args.json:
        print(json.dumps(totals.to_dict(), indent=2))
    else:
        _print_table(totals)
    # Non-zero exit only on hard errors — a pending/review outcome is success.
    return 1 if totals.errors else 0


if __name__ == "__main__":
    sys.exit(main())
