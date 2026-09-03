"""STAB-2026-09-01 §4 — the daily anchor must run after the last daily append.

`kai-truth-anchor` sealed the chain tip at 04:35 UTC while
`kai-canonical-edge-attest` appended a new `canonical_edge_report` at 06:20 UTC —
every day, 65 such records in the live ledger. The anchor was therefore stale
within two hours of being taken, and the tip spent most of each day unanchored.

The operator digest compounded it: its timer carried `OnCalendar=*-*-* 07:30:00`
with no UTC suffix, so on the Europe/Berlin host it fired at 05:30 UTC — inside
the only window in which the tip looked anchored. The one consumer built to catch
a silently failing anchor was scheduled where it could not see the failure.

This test pins the ordering as a unit-file invariant so neither half can drift back.
"""

from __future__ import annotations

import re
from pathlib import Path

SYSTEMD = Path(__file__).resolve().parents[2] / "deploy" / "systemd"

#: Daily writers of a truth-ledger record. `canonical_edge_report` is the only
#: regular daily append (LN / compliance records are single events).
DAILY_LEDGER_APPENDERS = ("kai-canonical-edge-attest",)
ANCHOR = "kai-truth-anchor"
DIGEST = "kai-operator-digest"


def _oncalendar(unit: str) -> str:
    text = (SYSTEMD / f"{unit}.timer").read_text(encoding="utf-8")
    match = re.search(r"^\s*OnCalendar=(.+)$", text, re.MULTILINE)
    assert match, f"{unit}.timer has no OnCalendar"
    return match.group(1).strip()


def _utc_seconds(expr: str) -> int:
    """Seconds-of-day for a `*-*-* HH:MM:SS UTC` expression. UTC is mandatory."""
    match = re.fullmatch(r"\*-\*-\*\s+(\d{2}):(\d{2}):(\d{2})\s+UTC", expr)
    assert match, f"expected an explicit-UTC daily expression, got {expr!r}"
    h, m, s = (int(g) for g in match.groups())
    return h * 3600 + m * 60 + s


def test_every_timer_in_the_anchor_chain_pins_utc() -> None:
    """A missing UTC suffix silently shifts by the host timezone."""
    for unit in (*DAILY_LEDGER_APPENDERS, ANCHOR, DIGEST):
        assert _oncalendar(unit).endswith("UTC"), f"{unit}.timer must pin UTC"


def test_truth_anchor_runs_after_the_last_daily_append() -> None:
    """LATEST_DAILY_APPEND_TIME < ANCHOR_TIME."""
    latest_append = max(_utc_seconds(_oncalendar(u)) for u in DAILY_LEDGER_APPENDERS)
    anchor = _utc_seconds(_oncalendar(ANCHOR))
    assert anchor > latest_append, (
        f"anchor at {anchor}s must run after the last daily append at {latest_append}s; "
        "otherwise the sealed tip is invalidated the same morning"
    )


def test_the_digest_reads_the_anchor_after_it_was_taken() -> None:
    """Otherwise 'Tip OTS-verankert' is a scheduling artefact, not a measurement."""
    assert _utc_seconds(_oncalendar(DIGEST)) > _utc_seconds(_oncalendar(ANCHOR))


def test_the_full_daily_order_holds() -> None:
    append = _utc_seconds(_oncalendar("kai-canonical-edge-attest"))
    anchor = _utc_seconds(_oncalendar(ANCHOR))
    digest = _utc_seconds(_oncalendar(DIGEST))
    assert append < anchor < digest, f"append={append} anchor={anchor} digest={digest}"
