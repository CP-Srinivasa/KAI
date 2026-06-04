"""Replay premium-signal parser/state invariants from a fixture.

Usage:
    python scripts/replay_premium_signals.py --fixture tests/fixtures/latest_premium_signals.json

Read-only: no Telegram, market data, bridge, paper engine, live switch, or Pi state.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.ingestion.telegram_channel_parser import (
    parse_premium_channel_message,
    parse_target_completion,
)
from app.premium.state_machine import bridge_stage_to_state, state_tone

REQUIRED_SYMBOLS = {
    "APR",
    "US",
    "NIGHT",
    "CYS",
    "AIO",
    "TRUTH",
    "BEAT",
    "HANA",
    "PHAROS",
    "BIRB",
    "DASH",
    "OPG",
    "IRYS",
    "BILL",
    "GUA",
}
SUCCESS_STATES = {"position_open", "partially_closed", "closed_tp", "reconciled_completion"}
TABLE_COLUMNS = [
    "Signal",
    "Parsed",
    "Envelope",
    "Approved",
    "Bridge",
    "Paper",
    "Position",
    "Completion",
    "Reconciled",
    "PnL",
    "Matrix",
    "Portfolio",
    "Quality",
    "Final State",
    "Error",
]


def _load_fixture(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("fixture root must be an object")
    return data


def _check_new_signals(rows: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    for row in rows:
        sig = parse_premium_channel_message(str(row.get("text") or ""))
        rid = row.get("id")
        if sig is None:
            errors.append(f"{rid}: new-signal did not parse")
            continue
        if sig.display_symbol != row.get("display_symbol"):
            errors.append(f"{rid}: symbol {sig.display_symbol} != {row.get('display_symbol')}")
        if "direction" in row and sig.direction != row.get("direction"):
            errors.append(f"{rid}: direction {sig.direction} != {row.get('direction')}")
        if "entry_value" in row and sig.entry_value != row.get("entry_value"):
            errors.append(f"{rid}: entry {sig.entry_value} != {row.get('entry_value')}")
        if "stop_loss" in row and sig.stop_loss != row.get("stop_loss"):
            errors.append(f"{rid}: sl {sig.stop_loss} != {row.get('stop_loss')}")
        if "min_targets" in row and len(sig.targets) < int(row["min_targets"]):
            errors.append(f"{rid}: targets {len(sig.targets)} < {row.get('min_targets')}")
    return errors


def _check_completions(rows: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    for row in rows:
        event = parse_target_completion(str(row.get("text") or ""))
        if event is None:
            errors.append(f"{row.get('id')}: completion did not parse")
            continue
        if event.display_symbol != row.get("display_symbol"):
            errors.append(
                f"{row.get('id')}: symbol {event.display_symbol} != {row.get('display_symbol')}"
            )
        if event.touch_price != row.get("touch_price"):
            errors.append(f"{row.get('id')}: touch {event.touch_price} != {row.get('touch_price')}")
    return errors


def _check_bridge_states(rows: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    for row in rows:
        state = bridge_stage_to_state(
            str(row.get("stage") or ""),
            str(row.get("reason") or ""),
        )
        tone = state_tone(state)
        if state.value != row.get("expected_state"):
            errors.append(f"{row.get('id')}: state {state.value} != {row.get('expected_state')}")
        if tone != row.get("expected_tone"):
            errors.append(f"{row.get('id')}: tone {tone} != {row.get('expected_tone')}")
    return errors


def _bool_cell(value: Any) -> str:
    return "yes" if bool(value) else "no"


def _format_row(row: dict[str, Any]) -> list[str]:
    final_state = str(row.get("final_state") or "")
    position = str(row.get("position") or "")
    reconciled = row.get("reconciled")
    if reconciled is None:
        reconciled = final_state == "reconciled_completion"
    portfolio = row.get("portfolio")
    if not portfolio:
        portfolio = "premium" if position in {"open", "partial", "closed"} else "excluded"
    return [
        str(row.get("signal") or ""),
        _bool_cell(row.get("parsed")),
        str(row.get("envelope") or ("accepted" if row.get("parsed") else "none")),
        _bool_cell(row.get("approved")),
        str(row.get("bridge") or ""),
        str(row.get("paper") or ""),
        position,
        str(row.get("completion") or ""),
        _bool_cell(reconciled),
        "" if row.get("pnl") is None else str(row.get("pnl")),
        str(row.get("matrix") or ""),
        str(portfolio),
        str(row.get("quality") or ""),
        final_state,
        str(row.get("error") or ""),
    ]


def _render_table(rows: list[dict[str, Any]]) -> str:
    table = [TABLE_COLUMNS, *[_format_row(row) for row in rows]]
    widths = [max(len(line[i]) for line in table) for i in range(len(TABLE_COLUMNS))]
    rendered = []
    for idx, line in enumerate(table):
        rendered.append("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(line)))
        if idx == 0:
            rendered.append("  ".join("-" * widths[i] for i in range(len(TABLE_COLUMNS))))
    return "\n".join(rendered)


def _signal_matrix_errors(rows: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    seen = {str(row.get("signal") or "").upper() for row in rows}
    missing = sorted(REQUIRED_SYMBOLS - seen)
    if missing:
        errors.append(f"missing signals: {', '.join(missing)}")
    for row in rows:
        signal = row.get("signal")
        final_state = str(row.get("final_state") or "")
        pnl = row.get("pnl")
        position = str(row.get("position") or "")
        if row.get("approved") and final_state in {"approved", "envelope_accepted"}:
            errors.append(f"{signal}: approved row has not reached execution decision")
        if pnl is not None and position not in {"open", "partial", "closed"}:
            errors.append(f"{signal}: pnl present without position lifecycle")
        if str(row.get("quality") or "") == "success" and final_state not in SUCCESS_STATES:
            errors.append(f"{signal}: success quality on non-success state {final_state}")
        if final_state in {"entry_disabled", "source_skipped", "bridge_rejected"} and pnl:
            errors.append(f"{signal}: blocked state carries pnl")
    return errors


def _invariant_results(rows: list[dict[str, Any]]) -> list[tuple[str, bool, str]]:
    errors = _signal_matrix_errors(rows)
    by_name: list[tuple[str, bool, str]] = [
        (
            "all_required_symbols_present",
            not any(error.startswith("missing signals:") for error in errors),
            "fixture includes the required premium symbols",
        ),
        (
            "no_pnl_without_position_lifecycle",
            not any("pnl present without position lifecycle" in error for error in errors),
            "PnL rows must be open/partial/closed lifecycle rows",
        ),
        (
            "success_quality_requires_success_state",
            not any("success quality on non-success state" in error for error in errors),
            "Quality success is limited to execution/outcome success states",
        ),
        (
            "blocked_states_do_not_carry_pnl",
            not any("blocked state carries pnl" in error for error in errors),
            "Entry-disabled/source-skipped/bridge-rejected rows do not book PnL",
        ),
        (
            "approved_is_not_final_success",
            not any("approved row has not reached execution decision" in error for error in errors),
            "Approved-only rows are not final trading success",
        ),
    ]
    return by_name


def _render_invariants(rows: list[dict[str, Any]]) -> str:
    lines = ["Invariant  Result  Detail", "---------  ------  ------"]
    for name, ok, detail in _invariant_results(rows):
        lines.append(f"{name:<41} {'PASS' if ok else 'FAIL':<6} {detail}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--fixture",
        type=Path,
        default=Path("tests/fixtures/latest_premium_signals.json"),
    )
    args = parser.parse_args()

    data = _load_fixture(args.fixture)
    signal_rows = data.get("signals") or []
    new_signal_rows = data.get("new_signals") or []
    completion_rows = data.get("completion_messages") or []
    bridge_rows = data.get("bridge_events") or []
    if not all(
        isinstance(x, list) for x in (signal_rows, new_signal_rows, completion_rows, bridge_rows)
    ):
        raise ValueError(
            "fixture lists missing: signals / new_signals / completion_messages / bridge_events"
        )

    errors = []
    errors.extend(_signal_matrix_errors(signal_rows))
    errors.extend(_check_new_signals(new_signal_rows))
    errors.extend(_check_completions(completion_rows))
    errors.extend(_check_bridge_states(bridge_rows))

    print(_render_table(signal_rows))
    print()
    print(_render_invariants(signal_rows))
    print(
        "premium replay:",
        f"signals={len(signal_rows)}",
        f"new_signals={len(new_signal_rows)}",
        f"completions={len(completion_rows)}",
        f"bridge_states={len(bridge_rows)}",
        f"errors={len(errors)}",
    )
    for error in errors:
        print(f"ERROR {error}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
